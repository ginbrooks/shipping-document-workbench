"""Local failure location only: never persist exception text, locals or documents."""
import traceback,uuid
from pathlib import Path
from shipping.repository import now


def record_processing_error(repo,exc,stage):
 error_id=uuid.uuid4().hex[:12]
 frames=[{'file':Path(frame.filename).name,'function':frame.name,'line':frame.lineno}
         for frame in traceback.extract_tb(exc.__traceback__)]
 try:
  repo.cache_set('agent-last-processing-error',{'error_id':error_id,'at':now(),
   'error_type':type(exc).__name__,'stage':stage,'frames':frames})
 except Exception:
  return None  # A diagnostic write failure must not hide the processing failure.
 return error_id
