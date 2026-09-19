"""One synthetic message through real intake, workspaces, validation and current heads."""
from __future__ import annotations
import json,shutil,tempfile
from pathlib import Path
from conversation_engine.bootstrap import bootstrap
from conversation_engine.config import EnginePaths,STAGES
from conversation_engine.db import Database
from conversation_engine.repository import Repository
from conversation_engine.service import EngineService
from conversation_engine.envelope import load_candidate


def demonstrate(root):
    paths=EnginePaths.from_root(root);database=Database(paths);database.initialize();connection=database.connect();repo=Repository(connection);bootstrap(repo)
    repo.publish_definition('mapping_index','discord_public',{'live_ready':True,'titles':[{'title':'Workshop','definition':'Synthetic workshop planning'}]})
    repo.set_control('intake',False,'Synthetic disposable demonstration')
    service=EngineService(paths,repo,intake_limit=20,work_limit=20)
    text='Morgan will review the fixture.'
    envelope={'chat':{'chat_id':'synthetic-workshop','destination_rel_path':'Archived/synthetic-workshop','position':1},'message':{'speaker':'User','timestamp':'2026-01-01T12:00:00+00:00','text':text}}
    source=paths.unprocessed/'source.json';source.parent.mkdir(parents=True,exist_ok=True);source.write_text(json.dumps(envelope),encoding='utf-8')
    accepted=service.intake.accept(load_candidate(source));returned=[]
    for _ in range(8):
        service.run_once()
        rows=connection.execute("SELECT workspace_id FROM workspaces WHERE state='published'").fetchall()
        for row in rows:
            ws=repo.workspace(row['workspace_id']);task=repo.task(ws['task_id']);stage=task['stage']
            todo=paths.workspace_todo_for_stage(stage)/ws['folder_name'];done=paths.workspace_done_for_stage(stage)/ws['folder_name']
            if stage=='mapping':(todo/'response.json').write_text(json.dumps({'discord_public_indexing':['Workshop']}),encoding='utf-8')
            elif stage=='public_safety':(todo/'response.json').write_text(json.dumps({'decision':'Public','redactions':[]}),encoding='utf-8')
            elif stage in {'original_versions','public_versions'}:(todo/'version_1.txt').write_text('Review the fixture.',encoding='utf-8')
            else:raise RuntimeError('Unexpected synthetic workspace stage: '+stage)
            # Both resolved targets are inside this newly created disposable root.
            assert todo.resolve().is_relative_to(Path(root).resolve()) and done.resolve().is_relative_to(Path(root).resolve())
            shutil.move(str(todo),str(done));returned.append(stage)
    result={'mode':'Synthetic worker replies; actual engine intake and validation','source':text,'public_message':repo.resolve_message_text(accepted.message_id,audience='public')['message'],'short_private_version':repo.resolve_message_text(accepted.message_id,audience='private',version=9)['message'],'completed_stages':{stage:repo.current_result(accepted.message_id,stage)['output']['kind'] if 'kind' in repo.current_result(accepted.message_id,stage)['output'] else 'mapping' for stage in STAGES},'worker_replies':returned,'live_model_calls':0}
    connection.close();return result


if __name__=='__main__':
    with tempfile.TemporaryDirectory(prefix='ce-demo-') as scratch:print(json.dumps(demonstrate(Path(scratch)/'ce'),indent=2))
