import json
from pathlib import Path
import time
import tkinter as tk

import pytest

from conftest import conversation
from memory_bridge.gui import BridgeApp
from memory_bridge.gui_service import run_operation, unique_destination
from test_browser_package import make_capture


@pytest.fixture(scope="module")
def tk_runtime():
    # One Tcl interpreter per process, as in the actual app. Repeated Tk creation on Windows
    # can fail while objects from an earlier threaded app are being garbage-collected.
    root = tk.Tk()
    root.withdraw()
    yield root
    root.destroy()


def clear_test_window(root):
    for pending in root.tk.call("after", "info"):
        root.after_cancel(pending)
    for child in root.winfo_children():
        child.destroy()


def wait_gui(app, timeout=15):
    end = time.monotonic() + timeout
    # Process the initial overview scheduled at 100ms as well as worker callbacks.
    while time.monotonic() < end:
        app.root.update()
        if not app.busy and not app.browser_ingest_queue:
            return
        time.sleep(.01)
    raise AssertionError("GUI task did not finish")


def test_background_service_full_migration_and_restore(tmp_path, make_zip):
    library = tmp_path / "library"
    destination = unique_destination(tmp_path / "exports")
    progress = []
    result = run_operation("migrate", library, {"zip": str(make_zip([conversation()])), "source": "account", "destination": destination}, progress.append)
    assert result["verification"]["ok"]
    assert result["extraction"]["candidates"] == 1
    assert len(progress) >= 3
    restored = tmp_path / "restored"
    assert run_operation("restore", library, {"package": destination, "destination": restored})["restored"]
    assert run_operation("verify_library", restored, {})["ok"]
    assert run_operation("search", restored, {"query": "中文"})
    memories = run_operation("memories", restored, {})
    mid = memories[0]["id"]
    assert run_operation("memory_update", restored, {"id": mid, "confirmed": True, "status": "active"})["confirmed"]
    assert run_operation("memory_history", restored, {"id": mid})[-1]["version"] == 2
    assert not run_operation("memory_rollback", restored, {"id": mid, "version": 1})["confirmed"]
    assert run_operation("memory_delete", restored, {"id": mid, "confirmed": True})["soft_deleted"] == mid


def test_partial_import_is_never_reported_as_complete(tmp_path, make_zip):
    result = run_operation("migrate", tmp_path / "lib", {"zip": make_zip(files={"chat.html": "only HTML"}),
        "source": "one", "destination": tmp_path / "package"})
    assert result["import"]["status"] == "partial"
    assert result["verification"]["ok"]
    assert "partial" in (tmp_path / "package/migration_report.md").read_text(encoding="utf-8")


def test_native_gui_worker_search_and_memory_edit(tmp_path, make_zip, monkeypatch, tk_runtime):
    errors = []
    monkeypatch.setattr("memory_bridge.gui.messagebox.showerror", lambda *a, **k: errors.append(a))
    root = tk_runtime
    root.withdraw()
    app = BridgeApp(root, tmp_path / "library", tmp_path / "results")
    try:
        # Prevent the startup refresh from racing this scripted test's first action.
        root.update()
        time.sleep(.12)
        root.update()
        wait_gui(app)
        app.zip_path.set(str(make_zip([conversation()])))
        app.import_zip(False)
        wait_gui(app)
        assert app.last_output is None
        app.toggle_library_all()
        app.prepare_handoff()
        wait_gui(app)
        assert app.last_output and (app.last_output / "manifest.json").exists()
        app.query.set("数学")
        app.search()
        wait_gui(app)
        assert app.search_rows
        app.search_tree.selection_set(next(iter(app.search_rows)))
        root.update()
        assert "数学" in app.search_detail.get("1.0", "end")
        app.extract();wait_gui(app)
        app.load_memories()
        wait_gui(app)
        mid = next(iter(app.memory_rows))
        app.memory_tree.selection_set(mid)
        root.update()
        app.confirm_memory()
        wait_gui(app)
        assert app.memory_rows[mid]["confirmed"]
        app.memory_tree.selection_set(mid)
        root.update()
        app.set_text(app.memory_text, "修改后的内容")
        app.edit_memory()
        wait_gui(app)
        assert app.memory_rows[mid]["content"] == "修改后的内容"
        assert not app.memory_rows[mid]["confirmed"]
        # A closed history popup must not leave a dead button in the busy-state loop.
        popup = app.show_document("history", "synthetic")
        app.button(popup, "temporary", lambda: None).pack()
        popup.destroy()
        app.refresh()
        wait_gui(app)
        assert not errors
    finally:
        root.after_cancel(app.poll_id)
        clear_test_window(root)


def test_failed_worker_releases_busy_state(tmp_path, monkeypatch, tk_runtime):
    messages = []
    monkeypatch.setattr("memory_bridge.gui.messagebox.showerror", lambda *a, **k: messages.append(a))
    root = tk_runtime
    root.withdraw()
    app = BridgeApp(root, tmp_path / "library", tmp_path / "results")
    try:
        app.start("verify_package", {"package": tmp_path / "nonexistent"})
        wait_gui(app)
        assert messages and not app.busy
        assert all(str(button["state"]) == "normal" for button in app.buttons)
    finally:
        root.after_cancel(app.poll_id)
        clear_test_window(root)


def test_unique_results_do_not_overwrite_previous_runs(tmp_path):
    assert unique_destination(tmp_path) != unique_destination(tmp_path)


def test_status_progress_remains_inside_window_at_high_dpi(tmp_path, tk_runtime):
    root = tk_runtime
    root.withdraw()
    root.tk.call("tk", "scaling", 1.7)
    app = BridgeApp(root, tmp_path / "lib", tmp_path / "results")
    try:
        root.geometry("1160x810")
        root.update_idletasks()
        assert app.spinner.winfo_y() >= 0
        bottom = app.spinner.winfo_rooty() - root.winfo_rooty() + app.spinner.winfo_height()
        assert bottom <= root.winfo_height()
    finally:
        root.after_cancel(app.poll_id)
        clear_test_window(root)


def test_official_export_helper_opens_only_official_pages(tmp_path, monkeypatch, tk_runtime):
    opened = []
    monkeypatch.setattr("memory_bridge.gui.webbrowser.open", lambda url, new: opened.append(url) or True)
    root = tk_runtime
    root.withdraw()
    app = BridgeApp(root, tmp_path / "lib", tmp_path / "results")
    try:
        app.official_export_helper()
        assert app.open_official_page("https://privacy.openai.com/")
        assert opened == ["https://privacy.openai.com/"]
        assert "尚未提交申请" in app.log.get("1.0", "end")
        for window in root.winfo_children():
            if isinstance(window, tk.Toplevel):
                window.destroy()
    finally:
        root.after_cancel(app.poll_id)
        clear_test_window(root)


def test_gui_browser_import_helper_and_migration_without_official_zip(tmp_path, monkeypatch, tk_runtime):
    errors=[]
    monkeypatch.setattr('memory_bridge.gui.messagebox.showerror',lambda *a,**k:errors.append(a))
    root=tk_runtime;root.withdraw()
    app=BridgeApp(root,tmp_path/'lib',tmp_path/'results')
    try:
        root.update();time.sleep(.12);root.update();wait_gui(app)
        app.browser_capture_helper()
        for child in root.winfo_children():
            if isinstance(child,tk.Toplevel):child.destroy()
        path=tmp_path/'browser.zip';make_capture(path)
        app.zip_path.set(str(path));app.import_zip(False);wait_gui(app)
        assert app.last_output is None
        app.toggle_library_all()
        app.prepare_handoff();wait_gui(app)
        assert app.last_import['stats']['input_kind']=='browser_extension'
        assert app.last_import['stats']['browser_coverage']['messages']==5
        assert app.last_output and (app.last_output/'browser_coverage.json').is_file()
        app.show_import_report()
        app.query.set('继续');app.search();wait_gui(app)
        assert len(app.search_rows)==2
        assert not errors
    finally:
        root.after_cancel(app.poll_id);clear_test_window(root)


def test_gui_storage_settings_switch_and_persistence(tmp_path,monkeypatch,tk_runtime):
    errors=[];monkeypatch.setattr('memory_bridge.gui.messagebox.showerror',lambda *a,**k:errors.append(a))
    root=tk_runtime;root.withdraw();config=tmp_path/'settings.json'
    app=BridgeApp(root,tmp_path/'old',tmp_path/'exports',settings_path=config)
    try:
        root.update();time.sleep(.12);root.update();wait_gui(app)
        app.storage_settings()
        for child in root.winfo_children():
            if isinstance(child,tk.Toplevel):child.destroy()
        app.apply_storage(tmp_path/'new',tmp_path/'newexports',True);wait_gui(app)
        assert app.data.get()==str((tmp_path/'new').resolve())
        assert app.output_parent.get()==str((tmp_path/'newexports').resolve())
        assert json.loads(config.read_text(encoding='utf-8'))['data_root']==app.data.get()
        assert (tmp_path/'old/bridge.sqlite3').is_file()
        assert not errors
    finally:
        root.after_cancel(app.poll_id);clear_test_window(root)


def test_gui_starts_on_export_and_routes_download_to_import(tmp_path,monkeypatch,tk_runtime):
    root=tk_runtime;root.withdraw()
    app=BridgeApp(root,tmp_path/'lib',tmp_path/'exports')
    selected=[]
    monkeypatch.setattr(app,'scan_backup_folder',lambda:selected.append(app.notebook.select()))
    try:
        assert app.notebook.select()==str(app.export_tab)
        assert '读取旧账号' in app.notebook.tab(0,'text')
        assert '资料库接续' in app.notebook.tab(1,'text')
        assert not app.zip_path.get()
        for helper,label in [(app.browser_capture_helper,'我已导出插件包'),(app.official_export_helper,'我已下载')]:
            app.notebook.select(app.export_tab)
            helper()
            button=next(b for b in app.buttons if b.winfo_exists() and b.cget('text').startswith(label))
            button.invoke()
            assert app.notebook.select()==str(app.import_tab)
        assert selected==[str(app.import_tab),str(app.import_tab)]
    finally:
        root.after_cancel(app.poll_id);clear_test_window(root)


def test_browser_selection_queue_and_multiple_received_zip_import(tmp_path,tk_runtime):
    from memory_bridge.browser_bridge import BrowserBridge
    root=tk_runtime;root.withdraw();app=BridgeApp(root,tmp_path/'lib',tmp_path/'output')
    bridge=BrowserBridge(tmp_path/'output',port=0);bridge.origin='chrome-extension://'+'a'*32
    app.browser_bridge=bridge
    try:
        root.update();time.sleep(.12);root.update();wait_gui(app)
        app.add_browser_items([{'url':'https://chatgpt.com/c/one','title':'日语'},
                              {'url':'https://chatgpt.com/c/two','title':'数学'}])
        app.toggle_browser_all();app.capture_browser_selected()
        assert len(bridge.tasks)==2
        assert len(app.browser_checked)==2
        task=next(iter(bridge.tasks))
        for name in ('one.zip','two.zip'):
            path=tmp_path/name;make_capture(path)
            bridge.events.put(('file',{'id':task,'path':str(path)}))
        bridge.events.put(('result',{'id':task,'status':'completed'}))
        app.poll_browser()
        assert len(app.zip_paths)==2 and app.zip_path.get().startswith('已选 2')
        app.import_received();assert app.notebook.select()==str(app.import_tab)
        app.import_zip(False);wait_gui(app)
        assert app.last_import
        from memory_bridge.store import Store
        with Store(tmp_path/'lib') as store:assert len(list(store.conversation_records()))==1
        app.stop_browser();assert all(t['status']=='cancelled' for t in bridge.tasks.values())
    finally:
        bridge.close();root.after_cancel(app.poll_id);clear_test_window(root)


def test_account_handoff_buttons_read_only_then_prepare_and_copy(tmp_path,make_zip,monkeypatch,tk_runtime):
    root=tk_runtime;root.withdraw();app=BridgeApp(root,tmp_path/'lib',tmp_path/'results')
    opened=[];copied=[];urls=[]
    monkeypatch.setattr('memory_bridge.gui.os.startfile',lambda path:opened.append(path))
    monkeypatch.setattr(root,'clipboard_clear',lambda:copied.clear())
    monkeypatch.setattr(root,'clipboard_append',lambda value:copied.append(value))
    monkeypatch.setattr('memory_bridge.gui.webbrowser.open',lambda url,**kwargs:urls.append(url) or True)
    def click(label):next(b for b in app.buttons if b.winfo_exists() and b.cget('text')==label).invoke()
    try:
        root.update();time.sleep(.12);root.update();wait_gui(app)
        assert not any('一键迁移' in b.cget('text') for b in app.buttons if b.winfo_exists())
        app.library_menu.invoke('打开 ChatGPT 网页');assert urls==['https://chatgpt.com/']
        app.zip_path.set(str(make_zip([conversation()])))
        app.import_zip(False);wait_gui(app)
        assert app.last_import and app.last_output is None and app.last_handoff is None
        assert not (tmp_path/'results').exists()
        app.toggle_library_all()
        click('生成上传资料');wait_gui(app)
        assert app.last_handoff and (app.last_handoff/'对话目录.json').is_file()
        assert app.last_handoff.name.startswith('桌面接续目录-')
        click('打开资料文件夹');assert opened==[str(app.last_handoff)]
        app.library_menu.invoke('复制桌面指令');assert str(app.last_handoff) in copied[0] and 'AGENTS.md' in copied[0]
        click('复制网页提示词');assert '上传文件' in copied[0] and str(app.last_handoff) not in copied[0]
    finally:
        root.after_cancel(app.poll_id);clear_test_window(root)


def test_prepare_handoff_requires_local_conversations(tmp_path):
    from memory_bridge.common import BridgeError
    with pytest.raises(BridgeError,match='先读取'):
        run_operation('prepare_handoff',tmp_path/'empty',{'destination':tmp_path/'upload'})
    assert not (tmp_path/'upload').exists()


def test_recursive_folder_list_imports_whole_volume_group_without_zip_picker(tmp_path,monkeypatch,tk_runtime):
    import shutil
    from test_volumes import split
    root=tk_runtime;root.withdraw();app=BridgeApp(root,tmp_path/'library',tmp_path/'exports')
    monkeypatch.setattr('memory_bridge.gui.filedialog.askopenfilenames',lambda **kwargs:pytest.fail('ZIP picker should not be used'))
    source=tmp_path/'logical.zip';make_capture(source);parts=split(source,tmp_path)
    folder=tmp_path/'outer'
    for index,part in enumerate(parts):
        target=folder/str(index)/'nested';target.mkdir(parents=True);shutil.copyfile(part,target/part.name)
    try:
        root.update();time.sleep(.12);root.update();wait_gui(app)
        app.load_catalog([folder]);wait_gui(app)
        assert len(app.library_rows)==1 and next(iter(app.library_rows.values()))['complete']
        app.toggle_library_all();app.read_selected_backups();wait_gui(app)
        assert app.library_view.get()=='已保存的对话' and len(app.library_rows)==1
        assert next(iter(app.library_rows.values()))['messages']==5
        app.toggle_library_all();app.prepare_handoff();wait_gui(app)
        assert app.last_handoff and (app.last_handoff/'AGENTS.md').is_file()
    finally:root.after_cancel(app.poll_id);clear_test_window(root)


@pytest.mark.parametrize('partial',[False,True])
def test_completed_browser_capture_enters_library_automatically(tmp_path,tk_runtime,partial):
    from memory_bridge.browser_bridge import BrowserBridge
    root=tk_runtime;root.withdraw();app=BridgeApp(root,tmp_path/'library',tmp_path/'exports')
    bridge=BrowserBridge(tmp_path/'exports',port=0);app.browser_bridge=bridge
    try:
        root.update();time.sleep(.12);root.update();wait_gui(app)
        app.add_browser_items([{'url':'https://chatgpt.com/c/synthetic-c1','title':'日语学习'}])
        row=next(iter(app.browser_rows.values()));app.browser_tasks['task']=row;app.browser_sources['task']='old-account'
        path=tmp_path/'capture.zip';make_capture(path)
        bridge.events.put(('file',{'id':'task','path':str(path)}))
        bridge.events.put(('result',{'id':'task','status':'completed','capture_status':'partial' if partial else 'captured'}))
        app.poll_browser();wait_gui(app)
        assert app.last_import and app.browser_tree.set(row,'status')==('部分备份已入库，可继续采集' if partial else '已入库')
        assert len(app.library_conversations)==1 and app.library_conversations[0]['messages']==5
    finally:bridge.close();root.after_cancel(app.poll_id);clear_test_window(root)


def test_storage_dialog_has_one_location_and_routes_outputs(tmp_path,tk_runtime):
    from tkinter import ttk
    root=tk_runtime;root.withdraw();app=BridgeApp(root,tmp_path/'old',tmp_path/'old-output',settings_path=tmp_path/'prefs.json')
    def descendants(widget):
        for child in widget.winfo_children():
            yield child
            yield from descendants(child)
    try:
        root.update();time.sleep(.12);root.update();wait_gui(app)
        app.storage_settings()
        window=next(w for w in root.winfo_children() if isinstance(w,tk.Toplevel))
        entries=[w for w in descendants(window) if isinstance(w,ttk.Entry)]
        assert len(entries)==1
        main=tmp_path/'all-data';entries[0].delete(0,'end');entries[0].insert(0,str(main))
        next(w for w in descendants(window) if isinstance(w,ttk.Button) and w.cget('text')=='保存并应用').invoke()
        wait_gui(app)
        assert app.data.get()==str(main/'资料库') and app.output_parent.get()==str(main)
        assert app.storage_root.get()==str(main)
        assert app.output_directory('接续目录')==main/'接续目录'
        assert app.output_directory('完整备份')==main/'完整备份'
    finally:root.after_cancel(app.poll_id);clear_test_window(root)


def test_simplified_ui_has_three_top_tabs_and_no_duplicate_actions(tmp_path,tk_runtime):
    root=tk_runtime;root.withdraw();app=BridgeApp(root,tmp_path/'library',tmp_path/'outputs')
    try:
        assert len(app.notebook.tabs())==3
        labels=[b.cget('text') for b in app.buttons if b.winfo_exists()]
        assert labels.count('存储设置…')==1
        assert '刷新概览' not in labels and '选择单个 ZIP（辅助）' not in labels
        assert not app.log.frame.winfo_manager()
        app.library_menu.invoke('处理记录');assert app.log.frame.winfo_manager()=='pack'
        app.library_menu.invoke('处理记录');assert not app.log.frame.winfo_manager()
    finally:root.after_cancel(app.poll_id);clear_test_window(root)


def test_header_shows_connection_and_capture_status(tmp_path,tk_runtime):
    import time
    from memory_bridge.browser_bridge import BrowserBridge
    root=tk_runtime;root.withdraw();app=BridgeApp(root,tmp_path/'library',tmp_path/'output')
    bridge=BrowserBridge(tmp_path,port=0);app.browser_bridge=bridge
    try:
        app.poll_browser();assert '等待' in app.plugin_status.get()
        bridge.connected=time.time();app.poll_browser();assert '已连接' in app.plugin_status.get()
        task=bridge.submit('capture',url='https://chatgpt.com/c/test');bridge.tasks[task]['status']='running'
        app.poll_browser();assert '采集中' in app.plugin_status.get()
        assert len(app.nav_buttons)==3 and app.navigation_bar.winfo_manager()=='pack'
    finally:bridge.close();root.after_cancel(app.poll_id);clear_test_window(root)


def test_blue_theme_corners_are_opaque_and_buttons_compact(tmp_path, tk_runtime):
    root=tk_runtime
    app=BridgeApp(root,tmp_path/'library',tmp_path/'out')
    try:
        root.update_idletasks()
        for image in root._rounded_images:
            assert image.get(0,0)==(255,255,255)
            assert not image.transparency_get(0,0)
        for button,page in app.nav_buttons:
            assert button.winfo_reqheight()<60
        assert app.nav_buttons[1][0].winfo_reqwidth()<180
    finally:
        root.after_cancel(app.poll_id);clear_test_window(root)


def test_main_actions_visible_at_minimum_window_size(tmp_path, tk_runtime):
    root=tk_runtime
    app=BridgeApp(root,tmp_path/'library',tmp_path/'out')
    try:
        root.geometry('1080x680');root.deiconify();root.update()
        for page in (app.export_tab,app.import_tab):
            app.notebook.select(page);root.update()
            visible=[button for button in app.buttons if button.winfo_ismapped()]
            assert visible
            for button in visible:
                assert button.winfo_height()>=button.winfo_reqheight()
                assert button.winfo_rooty()+button.winfo_height()<=root.winfo_rooty()+root.winfo_height()
        assert app.library_tree.winfo_height()>100
    finally:
        root.withdraw();root.after_cancel(app.poll_id);clear_test_window(root)


def test_conversation_context_menu_traces_original_zip_without_changing_checks(tmp_path,tk_runtime,monkeypatch):
    from types import SimpleNamespace
    root=tk_runtime;app=BridgeApp(root,tmp_path/'library',tmp_path/'exports')
    opened=[];monkeypatch.setattr('memory_bridge.gui.os.startfile',lambda path:opened.append(path))
    posted=[];monkeypatch.setattr(app.library_context_menu,'tk_popup',lambda x,y:posted.append((x,y)))
    try:
        root.update();time.sleep(.12);root.update();wait_gui(app)
        archive=tmp_path/'capture.zip';make_capture(archive);app.zip_path.set(str(archive));app.import_zip(False);wait_gui(app)
        root.deiconify();app.notebook.select(app.import_tab);root.update()
        row=next(iter(app.library_rows));box=app.library_tree.bbox(row)
        app.show_library_context(SimpleNamespace(y=box[1]+box[3]//2,x_root=40,y_root=60))
        assert posted==[(40,60)] and not app.library_checked
        assert app.library_tree.selection()==(row,)
        item=app.library_rows[row]
        files=run_operation('conversation_files',tmp_path/'library',{'pk':item['pk']})
        assert len(files)==1 and files[0]['exists']
        assert Path(files[0]['path']).read_bytes()==archive.read_bytes()
        app.library_context_menu.invoke('查看备份 ZIP 文件…');wait_gui(app)
        button=next(b for b in app.buttons if b.winfo_exists() and b.cget('text')=='打开所选 ZIP')
        button.invoke();assert opened==[files[0]['path']]
        app.library_context_row=row
        shown=[];monkeypatch.setattr(app,'show_document',lambda title,body:shown.append((title,body)))
        app.context_view_raw();wait_gui(app)
        assert shown and item['external_id'] in shown[0][1]
        assert not app.library_checked
    finally:
        root.withdraw();root.after_cancel(app.poll_id);clear_test_window(root)


def test_context_menu_reopens_after_cancel_action_and_refresh(tmp_path,tk_runtime,monkeypatch):
    root=tk_runtime;app=BridgeApp(root,tmp_path/'library',tmp_path/'out')
    posted=[];monkeypatch.setattr(app.library_context_menu,'tk_popup',lambda x,y:posted.append(app.context_item()['title']))
    monkeypatch.setattr(root,'clipboard_clear',lambda:None);copied=[]
    monkeypatch.setattr(root,'clipboard_append',lambda text:copied.append(text))
    try:
        root.update();time.sleep(.12);root.update();wait_gui(app)
        app.library_conversations=[{'pk':str(i),'external_id':str(i),'source':'old','title':f'对话{i}','messages':2,'date':''} for i in range(2)]
        app.render_library();root.deiconify();app.notebook.select(app.import_tab);root.update()
        assert not app.library_tree.bind('<Button-3>')
        assert app.library_tree.bind('<ButtonRelease-3>')
        for index in (0,1,0,1):
            row=f'conversation:{index}';box=app.library_tree.bbox(row)
            app.library_tree.event_generate('<ButtonRelease-3>',x=100,y=box[1]+box[3]//2)
            root.update()
            app.library_context_menu.invoke('复制对话名称')
            app.dismiss_library_context();app.render_library();root.update()
            assert root.grab_current() is None
        assert posted==['对话0','对话1','对话0','对话1'] and copied==posted
        assert not app.library_checked
    finally:
        root.withdraw();root.after_cancel(app.poll_id);clear_test_window(root)


def test_library_context_menu_can_cancel_and_permanently_delete(tmp_path,tk_runtime,monkeypatch):
    library = tmp_path/'library'
    archive = tmp_path/'capture.zip'
    make_capture(archive)
    run_operation('import',library,{'zip':str(archive),'source':'A'})
    root = tk_runtime
    app = BridgeApp(root,library,tmp_path/'output')
    answers = iter((False,True))
    monkeypatch.setattr('memory_bridge.gui.messagebox.askyesno',lambda *a,**kw: next(answers))
    try:
        root.update();time.sleep(.12);root.update();wait_gui(app)
        active_row, = app.library_rows
        app.library_context_row = active_row
        app.library_checked.add(active_row)
        app.library_context_menu.invoke('永久删除对话…')
        assert active_row in app.library_rows and not app.busy
        app.library_context_menu.invoke('永久删除对话…')
        wait_gui(app)
        assert not app.library_rows and active_row not in app.library_checked
        app.library_view.set('回收站')
        app.render_library()
        assert not app.library_rows
        assert not list((library/'archives').glob('*.zip'))
    finally:
        root.withdraw();root.after_cancel(app.poll_id);clear_test_window(root)
