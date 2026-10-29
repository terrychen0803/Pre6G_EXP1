"""Offline Job API simulations; no kubectl subprocess or GPU workload is run."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import yaml
from test_validation_runs import EXECUTOR as GT, PLANNER, EVALUATOR

ROOT = Path(__file__).resolve().parents[1]


class Clock:
    def __init__(self):
        self.value = 0
        self.delays = []
    def now(self):
        return self.value
    def sleep(self, seconds):
        self.delays.append(seconds)
        self.value += seconds


class GroundTruthMonitorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.output = Path(self.temp.name)
        source = yaml.safe_load((ROOT / 'examples/yolo26/formal-40min-source-job.yaml').read_text())
        ranking = {'schema_version': 'pre6g.provisional-ranking-result/v1', 'ranking_mode': 'research-provisional-model-output',
            'selected_node': 'worker-a', 'total_work_units': 52448, 'ranked': [
                {'node': node, 'predicted_runtime_ms_per_work_unit': 50, 'predicted_node_total_steady_power_w': 100}
                for node in ('worker-a', 'worker-b')]}
        discovery = {'adapter': 'yolo', 'work': {'unit': 'training_iteration', 'steps_per_epoch': 32, 'total_units': 52448}}
        self.plan, self.jobs = PLANNER.plan(source, ranking, discovery, validation_id='long-run', target_minutes=40)
        self.record = {'status': 'started', 'executed': True, 'job_states': {}}
        self.clock = Clock()
        self.api_states = ['Running', 'Running']
        self.calls = []

    def live_jobs(self):
        result = []
        for index, (job, state) in enumerate(zip(self.jobs, self.api_states)):
            actual = copy.deepcopy(job)
            actual['metadata']['uid'] = 'uid-' + str(index)
            actual['status'] = {'startTime': '2026-10-28T00:00:00Z', 'active': int(state == 'Running')}
            if state in ('Complete', 'Failed', 'FailureTarget'):
                actual['status']['conditions'] = [{'type': state, 'status': 'True',
                    'lastTransitionTime': '2026-10-29T05:00:00Z', 'reason': 'Finished' if state == 'Complete' else 'BackoffLimitExceeded',
                    'message': 'job terminal detail'}]
            if state == 'Complete':
                actual['status']['completionTime'] = '2026-10-29T05:00:00Z'
            result.append(actual)
        return {'items': result}

    def pods(self):
        result = []
        for index, (job, state) in enumerate(zip(self.jobs, self.api_states)):
            pod = copy.deepcopy(job['spec']['template'])
            pod['metadata'].update(name='pod-' + str(index), uid='pod-uid-' + str(index),
                ownerReferences=[{'kind': 'Job', 'uid': 'uid-' + str(index)}])
            pod['metadata']['labels']['job-name'] = job['metadata']['name']
            pod['spec']['nodeName'] = job['metadata']['labels']['pre6g.io/candidate-node']
            pod['status'] = {'phase': 'Running'}
            if state in ('Complete', 'Failed'):
                pod['status'].update(phase='Succeeded' if state == 'Complete' else 'Failed', containerStatuses=[{
                    'name': 'trainer', 'restartCount': 0, 'state': {'terminated': {
                        'exitCode': 0 if state == 'Complete' else 1,
                        'startedAt': '2026-10-28T00:00:00Z', 'finishedAt': '2026-10-29T05:00:00Z'}}}])
            result.append(pod)
        return {'items': result}

    def get(self, context, namespace, kind, label=None):
        if kind == 'jobs':
            return self.live_jobs()
        return self.pods() if kind == 'pods' else {'items': [{'reason': 'TestEvidence'}]}

    def api_run(self, command, **kwargs):
        self.calls.append(command)
        return SimpleNamespace(returncode=0, stdout='container logs\n', stderr='')

    def monitor(self, timeout=0):
        with patch.object(GT, '_get', side_effect=self.get), patch.object(GT, '_run', side_effect=self.api_run), \
             patch.object(GT.time, 'monotonic', side_effect=self.clock.now), patch.object(GT.time, 'sleep', side_effect=self.clock.sleep):
            return GT.monitor('ctx', self.plan, self.jobs, self.output, self.record, timeout)

    def assert_no_mutation(self):
        self.assertFalse(any(any(verb in call for verb in ('delete', 'patch', 'replace', 'wait')) for call in self.calls))

    def test_running_longer_than_three_hours_then_complete(self):
        polls = 0
        original = self.live_jobs
        def live():
            nonlocal polls
            polls += 1
            if self.clock.value >= 14400:
                self.api_states = ['Complete', 'Complete']
            return original()
        with patch.object(self, 'live_jobs', side_effect=live), patch('builtins.print'):
            status = self.monitor()
        self.assertEqual(status, 'jobs_completed')
        self.assertGreaterEqual(self.clock.value, 14400)
        self.assertTrue(all(delay == 30 for delay in self.clock.delays))
        self.assertTrue(self.record['ground_truth_valid'])
        self.assertTrue(all(s['completion_time'] for s in self.record['job_states'].values()))
        self.assertEqual(self.record['job_states']['worker-a']['trainer_elapsed_s'], 29 * 3600)
        self.assertTrue((self.output / 'validation-pods.json').exists())
        self.assertTrue((self.output / 'worker-b.log').read_text())
        self.assert_no_mutation()

    def test_second_node_failure_observed_while_first_is_running(self):
        self.api_states = ['Running', 'Failed']
        with patch.object(self.clock, 'sleep') as mocked:
            def advance(seconds):
                self.assertEqual(self.record['job_states']['worker-b']['status'], 'Failed')
                self.assertTrue((self.output / 'evidence/000001/validation-pods.json').exists())
                self.clock.value += seconds
                self.api_states[0] = 'Complete'
            mocked.side_effect = advance
            self.assertEqual(self.monitor(), 'jobs_failed')
        self.assertIn('BackoffLimitExceeded', json.dumps(self.record))
        self.assertFalse(self.record['ground_truth_valid'])
        with self.assertRaisesRegex(ValueError, 'incomplete or failed'):
            EVALUATOR.pod_intervals(json.loads((self.output / 'validation-pods.json').read_text()), self.plan)
        self.assert_no_mutation()

    def test_failure_target_is_not_treated_as_terminal_until_failed(self):
        self.api_states = ['Complete', 'FailureTarget']
        def advance(seconds):
            self.assertEqual(self.record['job_states']['worker-b']['status'], 'Failing')
            self.clock.value += seconds
            self.api_states[1] = 'Failed'
        with patch.object(self.clock, 'sleep', side_effect=advance):
            self.assertEqual(self.monitor(), 'jobs_failed')
        self.assertFalse(self.record['ground_truth_valid'])

    def test_optional_timeout_does_not_cancel_jobs(self):
        self.assertEqual(self.monitor(65), 'timed_out')
        self.assertEqual(self.clock.value, 65)
        self.assertEqual(self.clock.delays, [30, 30, 5])
        self.assertFalse(self.record['ground_truth_valid'])
        self.assert_no_mutation()

    def test_transport_failure_retries_without_classifying_job_as_failed(self):
        original = self.get
        failures = 0
        def flaky(*args):
            nonlocal failures
            if failures == 0:
                failures += 1
                raise RuntimeError('temporary API outage')
            self.api_states = ['Complete', 'Complete']
            return original(*args)
        with patch.object(self, 'get', side_effect=flaky):
            self.assertEqual(self.monitor(), 'jobs_completed')
        self.assertEqual(self.record['poll_error_count'], 1)
        self.assertTrue(self.record['ground_truth_valid'])

    def test_resume_rejects_replaced_job_uid(self):
        self.record['job_states'] = {'worker-a': {'uid': 'old-uid', 'job_name': self.jobs[0]['metadata']['name']}}
        self.assertEqual(self.monitor(), 'monitoring_failed')
        self.assertEqual(self.record['job_states']['worker-a']['status'], 'IdentityMismatch')
        self.assertFalse(self.record['ground_truth_valid'])

    def test_complete_but_missing_logs_is_not_valid_and_can_be_recollected(self):
        self.api_states = ['Complete', 'Complete']
        with patch.object(self, 'api_run', return_value=SimpleNamespace(returncode=1, stdout='', stderr='logs unavailable')):
            self.assertEqual(self.monitor(), 'collection_failed')
        self.assertFalse(self.record['ground_truth_valid'])
        self.assertEqual(self.monitor(), 'jobs_completed')
        self.assertTrue(self.record['ground_truth_valid'])
        self.assertTrue((self.output / 'evidence/000001').exists())
        self.assertTrue((self.output / 'evidence/000002').exists())

    def test_sources_and_generated_jobs_have_no_deadline_or_workload_changes(self):
        for filename in ('formal-40min-source-job.yaml', 'validation-source-job.yaml'):
            source = yaml.safe_load((ROOT / 'examples/yolo26' / filename).read_text())
            self.assertNotIn('activeDeadlineSeconds', source['spec'])
        for job in self.jobs:
            self.assertNotIn('activeDeadlineSeconds', job['spec'])
            args = job['spec']['template']['spec']['containers'][0]['args']
            self.assertIn('epochs=1639', args)
            self.assertIn('batch=16', args)
        self.assertEqual(self.plan['planned_total_work_units'], 52448)
        invalid = copy.deepcopy(self.jobs)
        invalid[0]['spec']['activeDeadlineSeconds'] = 10800
        with self.assertRaisesRegex(ValueError, 'activeDeadlineSeconds'):
            GT.check_inputs(self.plan, invalid)

    def cli(self, *flags):
        (self.output / 'plan.json').write_text(json.dumps(self.plan))
        (self.output / 'jobs.yaml').write_text(yaml.safe_dump_all(self.jobs))
        args = ['run_validation_jobs.py', '--plan', str(self.output / 'plan.json'), '--jobs', str(self.output / 'jobs.yaml'),
                '--kube-context', 'ctx', '--output-dir', str(self.output / 'run'), *flags]
        def api(command, **kwargs):
            self.calls.append(command)
            if command[:3] == ['kubectl', 'config', 'current-context']:
                return SimpleNamespace(returncode=0, stdout='ctx\n', stderr='')
            return SimpleNamespace(returncode=0, stdout='logs\n', stderr='')
        with patch('sys.argv', args), patch.object(GT, '_get', side_effect=self.get), patch.object(GT, '_run', side_effect=api), \
             patch.object(GT.time, 'monotonic', side_effect=self.clock.now), patch.object(GT.time, 'sleep', side_effect=self.clock.sleep), patch('builtins.print'):
            return GT.main()

    def test_cli_interrupt_then_resume_same_jobs_and_recollect(self):
        with patch.object(self.clock, 'sleep', side_effect=KeyboardInterrupt):
            self.assertEqual(self.cli('--attach'), 130)
        before = json.loads((self.output / 'run/ground-truth-run.json').read_text())
        self.assertEqual(before['status'], 'interrupted')
        self.assertEqual(before['timeout_seconds'], 0)
        self.assertFalse(before['ground_truth_valid'])
        self.assertTrue((self.output / 'run/validation-pods.json').exists())
        self.api_states = ['Complete', 'Complete']
        self.assertEqual(self.cli('--resume'), 0)
        after = json.loads((self.output / 'run/ground-truth-run.json').read_text())
        self.assertTrue(after['ground_truth_valid'])
        self.assertEqual(len(after['attempts']), 2)
        self.assertEqual(before['job_states']['worker-a']['uid'], after['job_states']['worker-a']['uid'])
        self.assertFalse(any('create' in call for call in self.calls))
        self.assert_no_mutation()

    def test_cli_timeout_then_resume_and_collect_already_complete_jobs(self):
        self.assertEqual(self.cli('--attach', '--timeout-seconds', '30'), 2)
        self.assertEqual(json.loads((self.output / 'run/ground-truth-run.json').read_text())['status'], 'timed_out')
        self.api_states = ['Complete', 'Complete']
        self.assertEqual(self.cli('--resume'), 0)
        self.assert_no_mutation()

    def test_cli_resume_rejects_changed_inputs(self):
        self.cli('--attach', '--timeout-seconds', '1')
        self.plan['planned_total_work_units'] = 1
        with self.assertRaises(SystemExit):
            self.cli('--resume')
        self.assertFalse(any('create' in call for call in self.calls))

    def test_cli_negative_timeout_rejected(self):
        with self.assertRaises(SystemExit):
            self.cli('--attach', '--timeout-seconds', '-1')
        self.assertEqual(self.calls, [])

    def test_interrupt_between_create_and_first_poll_can_resume(self):
        (self.output / 'plan.json').write_text(json.dumps(self.plan))
        (self.output / 'jobs.yaml').write_text(yaml.safe_dump_all(self.jobs))
        args = ['run_validation_jobs.py', '--plan', str(self.output / 'plan.json'), '--jobs', str(self.output / 'jobs.yaml'),
                '--kube-context', 'ctx', '--output-dir', str(self.output / 'run'), '--execute']
        created = False
        def api(command, **kwargs):
            nonlocal created
            self.calls.append(command)
            if 'create' in command:
                created = True
                raise KeyboardInterrupt
            if 'node' in command:
                return SimpleNamespace(returncode=0, stderr='', stdout=json.dumps({'status': {
                    'conditions': [{'type': 'Ready', 'status': 'True'}], 'allocatable': {'nvidia.com/gpu': '20'}}}))
            return SimpleNamespace(returncode=0, stderr='', stdout='ctx\n')
        def get(context, namespace, kind, label=None):
            if kind == 'jobs' and not created:
                return {'items': []}
            return self.get(context, namespace, kind, label)
        with patch('sys.argv', args), patch.object(GT, '_run', side_effect=api), patch.object(GT, '_get', side_effect=get), patch('builtins.print'):
            self.assertEqual(GT.main(), 130)
        record = json.loads((self.output / 'run/ground-truth-run.json').read_text())
        self.assertEqual(record['job_states']['worker-a']['uid'], 'uid-0')
        self.api_states = ['Complete', 'Complete']
        self.assertEqual(self.cli('--resume'), 0)
        self.assertEqual(sum('create' in call for call in self.calls), 1)
        self.assert_no_mutation()

    def test_incomplete_attempt_invalidates_prior_evaluation_export(self):
        (self.output / 'validation-pods.json').write_text(json.dumps({'items': [], 'ground_truth_valid': True}))
        self.monitor(1)
        self.assertFalse(json.loads((self.output / 'validation-pods.json').read_text())['ground_truth_valid'])

    def test_failed_exit_zero_or_multiple_pods_cannot_become_valid_ground_truth(self):
        self.api_states = ['Complete', 'Failed']
        self.assertFalse(GT._pod_result([self.pods()['items'][1]], 'worker-b')['eligible_for_ground_truth'])
        self.api_states = ['Complete', 'Complete']
        pod = self.pods()['items'][0]
        self.assertFalse(GT._pod_result([pod, pod], 'worker-a')['eligible_for_ground_truth'])
        self.assertTrue(GT._pod_result([pod], 'worker-a')['eligible_for_ground_truth'])

    def test_admission_injected_deadline_is_rejected_without_patching(self):
        original = self.live_jobs
        def injected():
            raw = original()
            raw['items'][0]['spec']['activeDeadlineSeconds'] = 10800
            return raw
        with patch.object(self, 'live_jobs', side_effect=injected):
            self.assertEqual(self.monitor(), 'monitoring_failed')
        self.assertFalse(self.record['ground_truth_valid'])
        self.assert_no_mutation()

    def test_sigterm_checkpoints_without_stopping_cluster_jobs(self):
        import signal
        previous = signal.getsignal(signal.SIGTERM)
        with patch.object(self.clock, 'sleep', side_effect=lambda _: signal.raise_signal(signal.SIGTERM)):
            self.assertEqual(self.cli('--attach'), 130)
        self.assertEqual(signal.getsignal(signal.SIGTERM), previous)
        self.assertEqual(json.loads((self.output / 'run/ground-truth-run.json').read_text())['status'], 'interrupted')
        self.assert_no_mutation()

    def test_restart_logs_and_failed_cli_exit_are_preserved(self):
        self.api_states = ['Complete', 'Failed']
        original = self.pods
        def restarted():
            raw = original()
            raw['items'][1]['status']['containerStatuses'][0]['restartCount'] = 1
            return raw
        with patch.object(self, 'pods', side_effect=restarted):
            self.assertEqual(self.cli('--attach'), 2)
        record = json.loads((self.output / 'run/ground-truth-run.json').read_text())
        self.assertEqual(record['status'], 'jobs_failed')
        self.assertFalse(record['ground_truth_valid'])
        self.assertTrue(any('--previous' in command for command in self.calls))
        self.assertTrue(list((self.output / 'run/evidence').glob('*/*.previous.log')))
