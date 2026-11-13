"""Packaged desktop entry; keep live libraries outside the application delivery folder."""
from pathlib import Path
import json
import os
import sys

from .gui import main
from .settings import load,user_settings_path


def launch():
    if len(sys.argv) == 5 and sys.argv[1] == '--self-check' and sys.argv[3] == '--report':
        from tempfile import TemporaryDirectory, gettempdir
        from .gui_service import run_operation
        with TemporaryDirectory(prefix='memory-bridge-check-') as directory:
            work = Path(directory).resolve()
            if work.parent != Path(gettempdir()).resolve():
                raise RuntimeError('invalid temporary check directory')
            result = run_operation('import', work/'library', {'zip':sys.argv[2], 'source':'synthetic-check'})
            result['read_did_not_generate_package']=not (work/'migration').exists()
            result.update(run_operation('prepare_handoff',work/'library',{'destination':work/'migration'}))
            catalog=run_operation('catalog',work/'library',{'folders':[]})
            picked=catalog['conversations'][0]['pk']
            workspace=run_operation('desktop_workspace',work/'library',{'selected':[picked],'destination':work/'desktop-workspace'})
            assert workspace['verification']['ok'] and workspace['conversations']==1
            result['library_workspace_check']={'selected_conversations':1,'verified':True,'uploaded':workspace['uploaded']}
            result['storage_check']=run_operation('configure_storage',work/'library',{'data_root':work/'relocated-library','export_root':work/'relocated-output','copy_existing':True,'settings_path':work/'preferences/settings.json'})
            unified=run_operation('configure_root',work/'relocated-library',{'storage_root':work/'unified-data','settings_path':work/'preferences/settings.json'})
            result['unified_storage_check']={'copied':unified['copied'],'verified':unified['verification'],'single_root':True}
            from .browser_bridge import BrowserBridge
            from urllib.request import Request,urlopen
            with_data=BrowserBridge(work/'browser-output',port=0)
            try:
                address=f'http://127.0.0.1:{with_data.server.server_port}'
                headers={'X-Bridge-Origin':'chrome-extension://'+'a'*32,'Content-Type':'application/json'}
                with_data.trusted_origins.add(headers['X-Bridge-Origin'])
                with urlopen(Request(address+'/connect',data=b'{}',headers=headers),timeout=5) as response:
                    token=json.load(response)['token']
                task=with_data.submit('list');headers['Authorization']='Bearer '+token
                with urlopen(Request(address+'/commands',headers=headers),timeout=5) as response:
                    assert json.load(response)['command']['id']==task
                result['browser_connection_check']={'paired':True,'command_delivered':True}
            finally:with_data.close()
            import tkinter as tk
            root = tk.Tk()
            root.withdraw()
            from .gui import BridgeApp
            ui=BridgeApp(root,work/'ui-library',work/'ui-outputs')
            assert ui.notebook.select()==str(ui.export_tab)
            result['export_selection_check']={'default_export_page':True,'selection_table':bool(ui.browser_tree.winfo_exists())}
            root.after_cancel(ui.poll_id)
            for pending in root.tk.call('after','info'):root.after_cancel(pending)
            root.update_idletasks()
            result['desktop_runtime'] = {'tk_version': str(root.tk.call('info', 'patchlevel')),
                                         'bundled_extension': (Path(getattr(sys, '_MEIPASS', ''))/'browser-extension/manifest.json').is_file()}
            root.destroy()
            Path(sys.argv[4]).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
        return
    home = Path(os.environ.get('LOCALAPPDATA') or Path.home()) / 'ChatGPTMemoryBridge'
    config_path = Path(sys.executable).parent / 'memory-bridge-settings.json'
    config = {}
    if getattr(sys, 'frozen', False) and config_path.is_file():
        try:
            config = json.loads(config_path.read_text(encoding='utf-8'))
        except (ValueError, OSError):
            pass
    saved=load(user_settings_path())
    previous = config.get('data_root')
    legacy=bool(previous and (Path(previous)/'bridge.sqlite3').is_file())
    data = saved.get('data_root') or (previous if legacy else str(home/'资料库'))
    requested_output = config.get('export_root')
    output = saved.get('export_root') or (requested_output if legacy and requested_output and Path(requested_output).parent.is_dir() else str(home))
    from tkinter import messagebox
    from .common import BridgeError
    from .reset import clear_upgrade_cache
    from .store import Store
    try:
        with Store(data) as store:
            clear_upgrade_cache(store, output)
    except (OSError, BridgeError) as exc:
        messagebox.showwarning('升级缓存未清理完毕', str(exc))
    main(data_root=data, export_root=output,settings_path=user_settings_path())


if __name__ == '__main__':
    launch()
