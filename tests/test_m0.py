import io
import zipfile
import pytest
from shipping.ingestion import unpack
from shipping.repository import Repository, RevisionConflict


def test_persistence_and_immutable_revisions(tmp_path):
    r = Repository(tmp_path)
    assert r.save('test', {'value': 1}, 0) == 1
    with pytest.raises(RevisionConflict):
        r.save('test', {'value': 2}, 0)
    r.save('test', {'value': 2}, 1)
    r = Repository(tmp_path)
    assert r.load('test')['value'] == 2
    assert r.load('test', 1)['value'] == 1


def test_import_dedup_and_zip_safety(tmp_path):
    r = Repository(tmp_path)
    a = r.put_file('test.pdf', b'test', 'unclassified')
    assert r.put_file('another.pdf', b'test', 'unclassified')['id'] == a['id']
    b = io.BytesIO()
    with zipfile.ZipFile(b, 'w') as z:
        z.writestr('../escape.pdf', 'bad')
    with pytest.raises(ValueError, match='UNSAFE'):
        unpack('test.zip', b.getvalue())
