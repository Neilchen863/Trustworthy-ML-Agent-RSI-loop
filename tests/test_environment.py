"""Environment contracts must stay consistent and participate in method freeze."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from rsi.environment import check_task
from rsi.method import REPO, dependency_files, _digest
from rsi.task import load_task


def test_real_task_contracts_and_freeze_coverage():
    frozen = set(dependency_files())
    for name in ('random_acts_of_pizza', 'insults'):
        task = load_task(name)
        assert task.environment == 'environment/manifest.json'
        assert task.evaluation == 'evaluation/submission.json'
        result = check_task(task.path, metadata_only=True)
        assert result['ok'], result
        for relative in ('environment/manifest.json', 'environment/prepare.sh', 'environment/check.py',
                         'evaluation/submission.json'):
            assert task.path / relative in frozen
    assert REPO / 'environments/aide/manifest.json' in frozen
    assert REPO / 'environments/aide/build_overlay.sh' in frozen


def test_missing_external_prerequisites_fail_without_opening_labels():
    result = check_task(REPO / 'tasks/insults', environ={}, which=lambda _: None)
    assert not result['ok']
    failures = {c['name'] for c in result['checks'] if not c['ok']}
    assert {'runner-root', 'data-root', 'SIF_PATH', 'RSI_OVERLAY_PATH', 'command:qsub'} <= failures


def test_preflight_external_paths_and_metric_mismatch(tmp_path):
    import shutil
    task = tmp_path / 'tasks/insults'
    shutil.copytree(REPO / 'tasks/insults', task)
    shutil.copytree(REPO / 'environments', tmp_path / 'environments')
    root = tmp_path / 'runner'
    shared = json.loads((tmp_path / 'environments/aide/manifest.json').read_text())
    for name in shared['external_runner']['required_files']:
        p = root / name
        p.parent.mkdir(parents=True, exist_ok=True)
        p.touch()
    image, overlay = tmp_path / 'image.sif', tmp_path / 'base.overlay'
    image.touch(); overlay.touch()
    data = tmp_path / 'data'
    prepared = data / 'detecting-insults-in-social-commentary/prepared'
    (prepared / 'public').mkdir(parents=True)
    (prepared / 'private').mkdir()
    for name in ('train.csv', 'test.csv'):
        (prepared / 'public' / name).touch()
    env = {'MLEBENCH_AIDE_ROOT': str(root), 'SIF_PATH': str(image), 'RSI_OVERLAY_PATH': str(overlay),
           'DATA_DIR': str(data)}
    result = check_task(task, environ=env, which=lambda _: '/mock/command')
    assert result['ok'], result
    contract = task / 'evaluation/submission.json'
    before = _digest([contract], root=tmp_path)
    spec = json.loads(contract.read_text())
    spec['metric'] = 'wrong_metric'
    contract.write_text(json.dumps(spec))
    assert _digest([contract], root=tmp_path) != before
    result = check_task(task, metadata_only=True)
    assert not result['ok']
    assert any(c['name'] == 'metric' and not c['ok'] for c in result['checks'])
