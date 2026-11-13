"""Native Windows-friendly Tk UI. Database and file work runs in a background thread."""
from __future__ import annotations

import json
import os
from pathlib import Path
from queue import Empty, Queue
import threading
import webbrowser
import tkinter as tk
import sys
from tkinter import filedialog, messagebox, ttk, simpledialog
from tkinter.scrolledtext import ScrolledText

from . import __version__
from .common import BridgeError
from .gui_service import run_operation, unique_destination

STATUS = {"pending": "待确认", "active": "有效", "outdated": "已过时", "conflict": "有冲突", "deleted": "已删除", "merged": "已合并"}



class BridgeApp:
    def __init__(self, root, data_root="data", export_root="outputs/迁移结果", settings_path=None, auto_connect=False):
        self.root, self.events = root, Queue()
        self.plugin_status=tk.StringVar(value="○ 插件未连接")
        self.auto_connect=auto_connect
        self.busy = False
        self.buttons = []
        self.memory_rows = {}
        self.search_rows = {}
        self.last_output = None
        self.last_handoff = None
        self.last_import = None
        self.browser_bridge = None
        self.cache_reset_at = None
        self.browser_rows = {}
        self.browser_checked = set()
        self.browser_tasks = {}
        self.browser_files = {}
        self.zip_paths = []
        self.library_conversations=[]
        self.trashed_conversations=[]
        self.pending_backups=[]
        self.library_rows={}
        self.library_checked=set()
        self.library_view=tk.StringVar(value='已保存的对话')
        self.browser_ingest_queue=[]
        self.browser_sources={}
        self.browser_partial=set()
        self.settings_path=settings_path
        self.data = tk.StringVar(value=str(Path(data_root).resolve()))
        self.output_parent = tk.StringVar(value=str(Path(export_root).resolve()))
        self.storage_root=tk.StringVar(value=str(Path(data_root).resolve().parent if Path(data_root).resolve().name=='资料库' and Path(data_root).resolve().parent==Path(export_root).resolve() else Path(data_root).resolve()))
        self.zip_path = tk.StringVar()
        self.source = tk.StringVar(value="old-account")
        self.query = tk.StringVar()
        self.topic = tk.StringVar()
        self.status_filter = tk.StringVar(value="全部")
        self.state = tk.StringVar(value="读取旧账号 → 本地资料库 → 新账号桌面 Work 按需接续")
        self.handoff_state = tk.StringVar(value="勾选对话并点击“本地分对话接续”，每条对话使用自己的目录与提示词。")
        self.restore_tasks = {}
        self.restore_report_path = None
        self.restore_cancel_requested = False
        self.restore_history = []
        self.counts = tk.StringVar(value="本地处理  ·  全量归档  ·  分支与历史保留")
        self.retry = tk.BooleanVar(value=False)
        root.title(f"ChatGPT Memory Bridge · v{__version__}")
        if sys.platform == "win32":
            icon = str(Path(__file__).with_name("MemoryBridge.ico"))
            root.iconbitmap(default=icon)
            root.iconbitmap(icon)
        root.geometry("1240x760")
        root.minsize(1080, 680)
        root.configure(background="#ffffff")
        style = ttk.Style(root)
        style.theme_use("clam")
        style.configure(".", font=("Microsoft YaHei UI", 10), background="#ffffff", foreground="#24364b")
        style.configure("TNotebook", borderwidth=0)
        style.configure("TNotebook.Tab", padding=(12, 11), background="#f3f3f3")
        style.map("TNotebook.Tab", background=[("selected", "#ffffff")])
        style.configure("TButton", padding=(13, 9), background="#f3f3f3", borderwidth=0)
        style.map("TButton", background=[("active", "#e5e5e5")])
        style.configure("Primary.TButton", background="#202123", foreground="white", font=("Microsoft YaHei UI", 10, "bold"))
        style.map("Primary.TButton", background=[("active", "#3a3a3a"), ("disabled", "#b4c7c1")])
        style.configure("Treeview", borderwidth=0, rowheight=38, fieldbackground="white", background="white")
        style.configure("Treeview.Heading", font=("Microsoft YaHei UI", 10, "bold"), padding=8)
        from .rounded_theme import install
        install(root)
        style.configure('TNotebook', borderwidth=0, bordercolor='#ffffff', lightcolor='#ffffff', darkcolor='#ffffff')
        style.configure('TNotebook.Tab', padding=(14,7), background='#f3f8ff', borderwidth=0)
        style.map('TNotebook.Tab', background=[('selected','#e7f2ff')], foreground=[('selected','#087bec')])
        style.layout('Treeview', [('Treeview.padding', {'sticky':'nswe','children':[('Treeview.treearea',{'sticky':'nswe'})]})])
        style.configure('Treeview', rowheight=36, borderwidth=0, foreground='#34495e')
        style.configure('Treeview.Heading', background='#f2f7ff', foreground='#627b95',
                        font=('Microsoft YaHei UI',10), padding=(10,9), relief='flat',
                        borderwidth=0, bordercolor='#f2f7ff', lightcolor='#f2f7ff', darkcolor='#f2f7ff')
        style.map('Treeview', background=[('selected','#e7f2ff')],foreground=[('selected','#087bec')])
        style.map('Treeview.Heading', background=[('active','#e7f2ff')])
        style.configure('Vertical.TScrollbar', background='#dce7f3', troughcolor='#ffffff',
                        borderwidth=0, arrowsize=12, lightcolor='#ffffff', darkcolor='#ffffff')
        style.layout('Vertical.TScrollbar', [('Vertical.Scrollbar.trough', {'sticky':'ns', 'children':[
            ('Vertical.Scrollbar.thumb', {'sticky':'nswe', 'expand':True})]})])
        style.configure('Vertical.TScrollbar', bordercolor='#ffffff', lightcolor='#dce7f3', darkcolor='#dce7f3', gripcount=0)
        style.configure('Horizontal.TProgressbar', background='#1687ff', troughcolor='#f0f6ff', borderwidth=0,
                        bordercolor='#f0f6ff', lightcolor='#1687ff', darkcolor='#1687ff')
        style.configure('TCombobox', padding=(8,5), bordercolor='#e1ebf5', lightcolor='#e1ebf5', darkcolor='#e1ebf5',
                        arrowsize=12, selectbackground='#ffffff', selectforeground='#34495e', arrowcolor='#7995b1')
        style.map('TCombobox',fieldbackground=[('readonly','#ffffff')],background=[('readonly','#f2f7ff')],
                  selectbackground=[('readonly','#ffffff')],selectforeground=[('readonly','#34495e')])
        body = ttk.Frame(root, padding=(24,18))
        body.pack(fill='both',expand=True)
        brand=ttk.Frame(body);brand.pack(fill='x')
        logo=tk.Canvas(brand,width=36,height=36,bg='#ffffff',highlightthickness=0)
        logo.create_oval(1,1,35,35,fill='#1687ff',outline='')
        logo.create_text(18,17,text='M',fill='white',font=('Segoe UI',16,'bold'))
        logo.pack(side='left',padx=(0,10))
        ttk.Label(brand,text='Memory Bridge',font=('Segoe UI',19,'bold'),foreground='#24364b').pack(side='left')
        ttk.Label(brand,textvariable=self.plugin_status,foreground='#4382bc').pack(side='right')
        ttk.Label(body,text='保存旧对话，在新账号继续。',foreground='#8a9bae').pack(anchor='w',pady=(4,12))
        navigation=tk.Frame(body,bg='#ffffff');navigation.pack(fill='x',pady=(0,12))
        self.navigation_bar=navigation
        workspace = ttk.Frame(body,style="Card.TFrame")
        workspace.pack(fill="x", pady=(0, 10))
        ttk.Label(workspace, text="数据保存位置",foreground='#758aa1').pack(side="left")
        self.data_entry = ttk.Entry(workspace, textvariable=self.storage_root, state="readonly")
        self.data_entry.pack(side="left", fill="x", expand=True, padx=10)

        ttk.Label(body, textvariable=self.counts, foreground="#8a9bae").pack(anchor="w", pady=(0, 10))
        style.layout("Main.TNotebook.Tab",[])
        self.notebook = ttk.Notebook(body,style="Main.TNotebook")
        self.notebook.pack(fill="both", expand=True)
        self.export_tab = self.tab(" ① 读取旧账号 ")
        self.import_tab = self.tab(" ② 资料库接续 ")
        self.tools_tab=self.tab(" 工具 ")
        tools=ttk.Notebook(self.tools_tab);tools.pack(fill='both',expand=True)
        self.search_tab=self.tab('搜索',tools)
        self.memory_tab=self.tab('记忆审核',tools)
        self.backup_tab=self.tab('备份与恢复',tools)
        self.button(workspace,"存储设置…",self.storage_settings).pack(side="left",padx=6)
        self.nav_buttons=[]
        for title,page in [('读取旧账号',self.export_tab),('已保存的对话',self.import_tab),('工具',self.tools_tab)]:
            button=ttk.Button(navigation,text=title,command=lambda page=page:self.notebook.select(page),style='Nav.TButton')
            button.pack(side='left',padx=(0,10));self.nav_buttons.append((button,page))
        def update_nav(event=None):
            for button,page in self.nav_buttons:button.configure(style='Selected.Nav.TButton' if self.notebook.select()==str(page) else 'Nav.TButton')
        self.notebook.bind('<<NotebookTabChanged>>',update_nav);update_nav()
        self.build_export()
        self.build_import()
        self.build_search()
        self.build_memories()
        self.build_backup()
        footer = ttk.Frame(body)
        # Reserve the status area before allocating the notebook; high DPI must not hide progress.
        footer.pack(side="bottom", fill="x", pady=(12, 0), before=self.notebook)
        self.spinner = ttk.Progressbar(footer, mode="indeterminate", length=130)
        self.spinner.pack(side="right")
        ttk.Label(footer, textvariable=self.state).pack(side="left", fill="x", expand=True)
        root.protocol("WM_DELETE_WINDOW", self.close)
        self.poll_id = root.after(80, self.poll)
        root.after(100, self.refresh)
        if auto_connect:root.after(250,self.ensure_browser)

    def button(self, parent, text, command, primary=False):
        button = ttk.Button(parent, text=text, command=command, style="Primary.TButton" if primary else "TButton")
        self.buttons.append(button)
        return button

    def tab(self, title, notebook=None):
        notebook=notebook or self.notebook
        frame = ttk.Frame(notebook, padding=(0,12,0,0))
        notebook.add(frame, text=title)
        return frame

    def more_menu(self,parent,items):
        menu=tk.Menu(parent,tearoff=False)
        for label,command in items:menu.add_command(label=label,command=command)
        control=ttk.Menubutton(parent,text='更多 ▾',menu=menu)
        control.pack(side='left',padx=6)
        self.buttons.append(control)
        return menu

    def toggle_log(self):
        if self.log.frame.winfo_manager():self.log.pack_forget()
        else:self.log.pack(fill='x',pady=5)

    def path_row(self, parent, label, variable, callback, button_text="选择文件夹"):
        frame = ttk.Frame(parent)
        frame.pack(fill="x", pady=7)
        ttk.Label(frame, text=label, width=12).pack(side="left")
        ttk.Entry(frame, textvariable=variable).pack(side="left", fill="x", expand=True, padx=8)
        self.button(frame, button_text, callback).pack(side="left")

    def go_to_import(self, choose=False):
        self.notebook.select(self.import_tab)
        if choose:
            self.choose_zip()

    def build_export(self):
        frame = self.export_tab
        ttk.Label(frame, text="读取旧账号", font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w")
        self.browser_state=tk.StringVar(value="先在浏览器登录旧账号，再连接插件、读取列表并勾选对话。")
        ttk.Label(frame,textvariable=self.browser_state,wraplength=760).pack(anchor='w',pady=6)
        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=6)
        for label,callback in [('读取对话列表',self.read_browser_list),('全选／取消',self.toggle_browser_all),('停止本轮',self.stop_browser)]:
            self.button(actions,label,callback).pack(side='left',padx=(0,6))
        lower=ttk.Frame(frame);lower.pack(side='bottom',fill='x',pady=(8,0))
        bottom=ttk.Frame(lower);bottom.pack(fill='x',pady=7)
        self.button(bottom,'开始采集',self.capture_browser_selected,True).pack(side='left')
        self.button(bottom,'从头重新采集…',lambda:self.capture_browser_selected(from_start=True)).pack(side='left',padx=(8,0))
        self.button(bottom,'打开资料库列表',self.import_received).pack(side='left',padx=8)
        helpers=ttk.Frame(lower);helpers.pack(fill='x',pady=3)
        self.capture_menu=self.more_menu(helpers,[('检测插件连接',self.connect_browser),('插件安装与更新',self.browser_capture_helper),('备用配对码',self.pair_browser),('添加对话链接',self.add_browser_url),('打开 ChatGPT',lambda:self.open_official_page('https://chatgpt.com/')),('官方导出说明',self.official_export_helper)])
        ttk.Label(helpers,text='旧账号标识').pack(side='left',padx=(10,4))
        ttk.Entry(helpers,textvariable=self.source,width=18).pack(side='left')
        ttk.Label(lower,text='采集完成后自动入库。大对话自动分卷，入库后仍是一条对话。',wraplength=760,foreground='#777777').pack(anchor='w',pady=4)

        self.browser_tree=self.tree(frame,[('chosen','选择',55),('title','对话标题',420),('status','导出状态',250)])
        self.browser_empty=self.empty_hint(self.browser_tree,'从旧账号带走你的对话','登录浏览器后，点击上方“读取对话列表”')
        self.browser_tree.configure(height=4)
        self.browser_tree.bind('<ButtonRelease-1>',self.toggle_browser_row)

    def ensure_browser(self):
        if self.browser_bridge is None:
            try:
                from .browser_bridge import BrowserBridge
                self.browser_bridge=BrowserBridge(self.output_parent.get())
                self.browser_bridge.cache_reset_at=self.cache_reset_at
            except OSError:
                self.plugin_status.set('○ 连接服务被占用');return False
        return True

    def connect_browser(self):
        if self.ensure_browser():self.browser_state.set('正在自动连接插件。请确认插件已更新并启用；无需输入配对码。')

    def pair_browser(self):
        if not self.ensure_browser():return
        self.root.clipboard_clear();self.root.clipboard_append(self.browser_bridge.code)
        self.browser_state.set('备用配对码 '+self.browser_bridge.code+' 已复制；仅在自动识别失败时填入插件弹窗。')

    def require_browser(self):
        if not self.browser_bridge or not self.browser_bridge.origin:
            self.connect_browser();return False
        return True

    def stop_browser(self):
        if self.browser_bridge:
            self.browser_bridge.cancel()
            for row in self.browser_tasks.values():
                if self.browser_tree.set(row,'status') in ('排队中','采集中'):self.browser_tree.set(row,'status','已停止')
            self.browser_state.set('本轮已停止，已传完的 ZIP 和浏览器断点保留；未传完的 .part 文件不导入。')

    def interrupt_upload(self):
        self.restore_cancel_requested = True
        if not self.browser_bridge:
            self.handoff_state.set('当前没有正在上传的任务。');return
        self.browser_bridge.cancel(kind='restore')
        self.poll_browser()
        self.handoff_state.set('上传已中断，后续对话已停止。已发送的内容保留在对应聊天，正在传输的一批需核对网页。')

    def read_browser_list(self):
        if not self.require_browser():return
        import time
        if not any(task['kind']=='list' and task['status'] in ('queued','dispatched','running')
                   for task in self.browser_bridge.tasks.values()):
            self.browser_bridge.submit('list')
        if time.time()-self.browser_bridge.connected>=12:
            self.browser_state.set('等插件连接后自动读取。请在装有插件的浏览器里打开 ChatGPT。')
        else:
            self.browser_state.set('正在读取浏览器侧边栏，请保持 ChatGPT 网页打开…')

    def add_browser_items(self,items):
        import re
        for item in items:
            url=item.get('url','')
            if not re.fullmatch(r'https://chatgpt\.com/c/[A-Za-z0-9-]+',url):continue
            if url in self.browser_rows:continue
            row=self.browser_tree.insert('','end',values=('',str(item.get('title') or url),'待选择'))
            self.browser_rows[url]=row
        if self.browser_rows:self.browser_empty.place_forget()

    def add_browser_url(self):
        url=simpledialog.askstring('添加对话','粘贴旧账号对话链接（https://chatgpt.com/c/…）：',parent=self.root)
        if url:self.add_browser_items([{'url':url.strip().rstrip('/'),'title':url.strip()}])

    def toggle_browser_row(self,event):
        if self.browser_tree.identify_column(event.x)!='#1':return
        row=self.browser_tree.identify_row(event.y)
        if not row:return
        if row in self.browser_checked:self.browser_checked.remove(row)
        else:self.browser_checked.add(row)
        self.browser_tree.set(row,'chosen','✓' if row in self.browser_checked else '')

    def toggle_browser_all(self):
        rows=set(self.browser_rows.values())
        self.browser_checked=set() if self.browser_checked==rows else rows
        for row in rows:self.browser_tree.set(row,'chosen','✓' if row in self.browser_checked else '')

    def capture_browser_selected(self,from_start=False):
        if not self.require_browser():return
        if any(t['status'] in ('queued','dispatched','running') for t in self.browser_bridge.tasks.values()):
            self.browser_state.set('已有浏览器任务在进行，请等本轮完成；可在网页插件面板暂停。');return
        urls=[u for u,row in self.browser_rows.items() if row in self.browser_checked]
        if not urls:self.browser_state.set('请先在列表的“选择”列勾选要导出的对话。');return
        if from_start and not messagebox.askyesno('从头重新采集',f'从头重新采集所选的 {len(urls)} 条对话？本轮会重建这些对话的续采断点。',parent=self.root):
            return
        self.browser_bridge.output=Path(self.output_parent.get()).resolve()
        source=self.source.get().strip() or 'old-account'
        for url in urls:
            task=self.browser_bridge.submit('capture_from_start' if from_start else 'capture',url=url,
                title=self.browser_tree.set(self.browser_rows[url],'title'),source=source,library=self.data.get())
            self.browser_tasks[task]=self.browser_rows[url];self.browser_tree.set(self.browser_rows[url],'status','排队中')
            self.browser_sources[task]=source
        self.browser_state.set(f'已排队 {len(urls)} 条对话，插件将依次打开、采集并保存到数据位置下的“浏览器备份”。')

    def poll_browser(self):
        import time
        live=self.browser_bridge and time.time()-self.browser_bridge.connected<12
        active=live and any(t['status'] in ('dispatched','running') for t in self.browser_bridge.tasks.values())
        self.plugin_status.set('● 已连接 · 采集中' if active else '● 插件已连接' if live else '○ 等待插件自动连接')
        if not self.browser_bridge:return
        try:
            while True:
                kind,data=self.browser_bridge.events.get_nowait()
                if kind=='connected':self.browser_state.set('插件已连接，点击“读取对话列表”开始。')
                elif kind=='file':self.browser_files.setdefault(data['id'],[]).append(data['path'])
                elif kind=='result':
                    if data['id'] in self.restore_tasks:
                        restored=self.restore_tasks[data['id']]
                        if self.browser_bridge.tasks[data['id']]['status']=='cancelled' and data['status']!='cancelled':continue
                        state={'running':'导入中','completed':'已导入新账号','error':'导入暂停','cancelled':'已中断上传'}[data['status']]
                        self.handoff_state.set(data.get('error') or data.get('message') or state)
                        restored.update(status=data['status'],chat_url=data.get('chat_url') or restored.get('chat_url',''),
                                        error=data.get('error',''),sent_files=data.get('sent_files',restored.get('sent_files',0)))
                        if data.get('rename_status'):
                            restored.update(rename_status=data['rename_status'],rename_error=data.get('rename_error',''))
                        for field in ('phase','final_sent','submission_uncertain','pending_marker'):
                            if field in data:restored[field]=data[field]
                        if data['status']=='error':
                            for task,record in self.restore_tasks.items():
                                if self.browser_bridge.tasks[task]['status']=='cancelled':
                                    record['status']='cancelled'
                            self.handoff_state.set('导入暂停：'+data.get('error','')+'。后续对话已停止，请检查已打开的聊天。')
                        elif data['status']=='completed':
                            completed=sum(record['status']=='completed' for record in self.restore_tasks.values())
                            self.handoff_state.set(f'已完成 {completed}/{len(self.restore_tasks)} 条对话。'+data.get('message',''))
                        if data['status'] in ('running','completed','error','cancelled'):
                            report=self.restore_report_path
                            report.write_text(json.dumps(self.restore_history+list(self.restore_tasks.values()),ensure_ascii=False,indent=2),encoding='utf-8')
                        if data['status'] in ('completed','error','cancelled'):
                            self.append_log(state+'：'+restored['title']+' '+restored.get('chat_url',''))
                        continue
                    row=self.browser_tasks.get(data['id'])
                    if row:
                        status={'running':'采集中','completed':'已导出','error':'未完成'}[data['status']]
                        self.browser_tree.set(row,'status',status)
                        error=data.get('error')
                        if error=='未知软件任务' and self.browser_bridge.tasks.get(data['id'],{}).get('kind')=='capture_from_start':
                            error='请在浏览器扩展管理页重新加载插件，再从头重新采集。'
                        message=error or data.get('message') or status
                        held=data.get('held_tasks',[])
                        for task in held:
                            if pending_row:=self.browser_tasks.get(task):
                                self.browser_tree.set(pending_row,'status','本轮已暂停')
                        if held:message+=' 后续对话已暂停。可单独勾选当前对话重试。'
                        self.browser_state.set(message)
                        if data['status']=='completed':
                            if data.get('capture_status')=='partial':self.browser_partial.add(data['id'])
                            else:self.browser_partial.discard(data['id'])
                            self.zip_paths=list(dict.fromkeys([*self.zip_paths,*self.browser_files.get(data['id'],[])]))
                            self.zip_path.set(f'已选 {len(self.zip_paths)} 个 ZIP（含同一对话分卷）')
                            paths=self.browser_files.get(data['id'],[])
                            if paths:
                                self.browser_ingest_queue.append((data['id'],paths,self.browser_sources.get(data['id'],self.source.get().strip() or 'old-account')))
                                self.browser_tree.set(row,'status','已保存，等待入库')
                    elif data['status']=='completed':
                        self.add_browser_items(data.get('items',[]))
                        self.browser_state.set(f'已发现 {len(self.browser_rows)} 条对话。'+data.get('note',''))
                    elif data['status']=='error':self.browser_state.set(data.get('error','读取失败，请重新读取。'))
        except Empty:pass

    def import_received(self):
        self.go_to_import(False)
        self.load_catalog([self.output_parent.get()],False)

    def build_import(self):
        frame = self.import_tab
        ttk.Label(frame, text="已保存的对话", font=("Microsoft YaHei UI", 15, "bold")).pack(anchor="w")
        ttk.Label(frame,text='① 勾选旧对话　→　② 本地分对话接续　→　③ 一个本地项目，每条对话分别开聊天。',foreground='#8a9bae').pack(anchor='w',pady=(4,2))
        helper = ttk.Frame(frame)
        helper.pack(fill="x", pady=(5, 8))
        view=ttk.Combobox(helper,textvariable=self.library_view,values=['已保存的对话','从备份文件添加','回收站'],state='readonly',width=17)
        view.pack(side='left');view.bind('<<ComboboxSelected>>',lambda _:self.render_library())
        self.button(helper,'查找已有备份',self.scan_backup_folder).pack(side='left',padx=6)
        self.button(helper,'刷新列表',self.refresh).pack(side='left')
        self.button(helper,'全选／取消',self.toggle_library_all).pack(side='left',padx=6)
        self.button(helper,'添加所选备份',self.read_selected_backups).pack(side='left')
        lower=ttk.Frame(frame);lower.pack(side='bottom',fill='x',pady=(8,0))
        actions = ttk.Frame(lower)
        actions.pack(fill="x", pady=6)
        self.button(actions, "本地分对话接续", self.local_selected,True).pack(side="left")
        self.button(actions, "网页 Work 批量导入…", self.restore_selected).pack(side="left",padx=6)
        ttk.Button(actions,text='中断上传',command=self.interrupt_upload).pack(side='left',padx=6)
        ttk.Button(actions,text='继续未完成上传…',command=self.resume_upload).pack(side='left',padx=6)
        self.button(actions, "生成上传资料", self.prepare_handoff).pack(side="left",padx=6)
        self.button(actions, "打开资料文件夹", self.open_handoff).pack(side="left",padx=6)
        self.button(actions, "复制网页提示词", self.copy_web_prompt).pack(side="left")
        self.library_menu=self.more_menu(actions,[('清理接续目录…',self.cleanup_workspaces),('停止导入',self.stop_browser),('打开 ChatGPT 网页',self.open_account_page),('桌面使用说明',self.desktop_steps),('复制桌面指令',self.copy_handoff_prompt),('查看读取报告',self.show_import_report),('处理记录',self.toggle_log)])
        ttk.Label(lower,textvariable=self.handoff_state,wraplength=760,foreground='#6b6b6b').pack(anchor='w',pady=4)
        details=ttk.Frame(lower);details.pack(fill='x',pady=3)
        ttk.Label(details,text='旧账号备注').pack(side='left',padx=(10,4))
        ttk.Entry(details,textvariable=self.source,width=18).pack(side='left')
        ttk.Label(details,text='仅添加旧备份时填写；同一旧账号保持一致。',foreground='#8a9bae').pack(side='left',padx=8)
        self.log = ScrolledText(lower, height=2, wrap="word", font=("Microsoft YaHei UI", 9), borderwidth=0, padx=8, pady=4)
        self.log.configure(state="disabled")

        self.library_tree=self.tree(frame,[('chosen','选择',50),('title','对话标题',330),('source','旧账号／备份数量',150),('info','消息数量／读取状态',130),('date','记录时间',165)])
        self.library_empty=self.empty_hint(self.library_tree,'这里会收好你的对话','先读取旧账号，或扫描已有备份文件夹')
        self.library_tree.configure(height=4)
        self.library_tree.bind('<ButtonRelease-1>',self.toggle_library_row)
        self.library_context_row=None
        self.library_context_menu=tk.Menu(self.library_tree,tearoff=False)
        for label,command in [('查看原始对话记录',self.context_view_raw),('查看备份 ZIP 文件…',lambda:self.context_files(False)),
                              ('打开备份所在文件夹…',lambda:self.context_files(True)),('复制对话名称',self.context_copy_title)]:
            self.library_context_menu.add_command(label=label,command=command)
        self.library_context_menu.add_separator()
        self.library_context_menu.add_command(label='归入已有对话…',command=self.context_attach_continuation)
        self.library_context_menu.add_command(label='查看或拆分续聊…',command=self.context_manage_continuation)
        self.library_context_menu.add_separator()
        self.library_context_menu.add_command(label='永久删除对话…',command=self.context_delete_conversation)
        self.library_context_menu.add_command(label='永久删除勾选项…',command=lambda:self.context_delete_conversation(checked=True))
        self.library_context_menu.add_command(label='恢复对话',command=self.context_restore_conversation)
        # Windows native menus consume mouse events while posted. Open after the
        # right button is released, so its pending release cannot strand tracking.
        self.library_tree.bind('<ButtonRelease-3>',self.show_library_context)
        self.library_context_menu.bind('<Unmap>',self.release_library_context)
        self.library_tree.bind('<Shift-F10>',self.show_library_context_keyboard)

    def show_library_context(self,event):
        self.dismiss_library_context()
        row=self.library_tree.identify_row(event.y)
        if row not in self.library_rows:return
        self.library_tree.focus_set()
        self.library_tree.selection_set(row);self.library_tree.focus(row)
        self.library_context_row=row
        item = self.library_rows[row]
        trashed = item.get('trashed', False)
        available = 'disabled' if item['backup'] or trashed else 'normal'
        self.library_context_menu.entryconfigure(0,state=available)
        for index in (1, 2):
            self.library_context_menu.entryconfigure(index,state='disabled' if trashed else 'normal')
        self.library_context_menu.entryconfigure('归入已有对话…',state=available)
        self.library_context_menu.entryconfigure('查看或拆分续聊…',state=available if len(item.get('parts',[]))>1 else 'disabled')
        self.library_context_menu.entryconfigure('永久删除对话…',state='disabled' if item['backup'] else 'normal')
        checked_count = sum(key in self.library_checked and not record['backup'] for key,record in self.library_rows.items())
        self.library_context_menu.entryconfigure('永久删除勾选项…',state='normal' if checked_count>1 else 'disabled')
        self.library_context_menu.entryconfigure('恢复对话',state='normal' if trashed else 'disabled')
        try:self.library_context_menu.tk_popup(event.x_root,event.y_root)
        finally:self.release_library_context()
        return 'break'

    def release_library_context(self,event=None):
        try:
            if self.root.grab_current()==self.library_context_menu:
                self.library_context_menu.grab_release()
        except tk.TclError:pass

    def dismiss_library_context(self):
        try:self.library_context_menu.unpost()
        except tk.TclError:pass
        self.release_library_context()

    def show_library_context_keyboard(self,event):
        row=self.library_tree.focus()
        if row not in self.library_rows:return
        box=self.library_tree.bbox(row)
        if not box:return
        from types import SimpleNamespace
        return self.show_library_context(SimpleNamespace(y=box[1]+box[3]//2,
            x_root=self.library_tree.winfo_rootx()+40,y_root=self.library_tree.winfo_rooty()+box[1]+box[3]))

    def context_item(self):
        return self.library_rows.get(self.library_context_row)

    def context_delete_conversation(self, checked=False):
        item = self.context_item()
        if not item or item['backup'] or self.busy:
            return
        selected = [(key,record) for key,record in self.library_rows.items()
                    if key in self.library_checked and not record['backup']] if checked else [(self.library_context_row,item)]
        if not selected:
            return
        records = [record for _,record in selected]
        if len(records)==1:
            label = f'「{records[0]["title"]}」'
        else:
            names = '\n'.join(f'• {record["title"]} · {record["source"]}' for record in records[:8])
            if len(records)>8:
                names += f'\n另有 {len(records)-8} 条勾选记录'
            label = f'以下 {len(records)} 条对话\n{names}'
        message = (f'永久删除{label}？\n\n'
                   '将删除资料库里的对话、历史版本、专属记忆及原始 ZIP，无法从回收站恢复。')
        parts = [part for record in records for part in (record.get('parts') or [record])]
        if len(parts) > len(records):
            message += f'\n\n所选对话共含 {len(parts)} 段记录，将一起删除。只删其中一段时，请先拆分该段。'
        if not messagebox.askyesno('永久删除对话',message,parent=self.root,default='no'):
            return

        def done(result):
            self.library_checked.difference_update(key for key,_ in selected)
            self.search_tree.delete(*self.search_tree.get_children())
            self.search_rows.clear()
            self.memory_tree.delete(*self.memory_tree.get_children())
            self.memory_rows.clear()
            self.set_text(self.search_detail,'请重新搜索。')
            self.set_text(self.memory_text,'')
            self.memory_source.set('请选择记忆。')
            if result['pending_archives']:
                self.handoff_state.set('对话已删除。有 ZIP 暂时无法清理，关闭占用文件的程序后可刷新列表重试。')
            else:
                self.handoff_state.set(f'已永久删除 {len(records)} 条对话。')
            self.refresh()
        self.start('delete_conversation',{'pks':[record['pk'] for record in records],'members':[part['pk'] for part in parts],
                                          'confirmed':True},done)

    def context_restore_conversation(self):
        item = self.context_item()
        if not item or not item.get('trashed') or self.busy:
            return

        def done(_):
            self.handoff_state.set(f'已恢复「{item["title"]}」，可在“已保存的对话”查看。')
            self.refresh()
        self.start('restore_conversation',{'pk':item['pk']},done)

    def context_copy_title(self):
        item=self.context_item()
        if item:
            self.root.clipboard_clear();self.root.clipboard_append(item['title'])

    def context_view_raw(self):
        item=self.context_item()
        if not item or item['backup']:return
        self.start('conversation_track',{'pk':item['pk']},
            lambda records:self.show_document('原始对话记录 · '+item['title'],json.dumps(records,ensure_ascii=False,indent=2)))

    def context_attach_continuation(self):
        item = self.context_item()
        if not item or item['backup'] or self.busy:
            return
        candidates = [row for row in self.library_conversations if row['pk'] != item['pk']]
        if not candidates:
            messagebox.showinfo('归入已有对话','资料库里还没有其他对话。',parent=self.root)
            return
        title = (item['title'] or '').strip().casefold()
        candidates.sort(key=lambda row: bool(title and title == (row['title'] or '').strip().casefold()),reverse=True)
        window = tk.Toplevel(self.root)
        window.title('归入已有对话')
        panel = ttk.Frame(window,padding=18)
        panel.pack(fill='both',expand=True)
        ttk.Label(panel,text=f'将「{item["title"]}」接到以下哪条对话后面？').pack(anchor='w',pady=(0,10))
        values = [f'{i}. {row["title"]} · {row["source"]}' for i,row in enumerate(candidates,1)]
        choice = ttk.Combobox(panel,values=values,state='readonly',width=58)
        choice.pack(fill='x')
        def save():
            if choice.current() < 0:
                return
            target = candidates[choice.current()]
            window.destroy()
            def done(_):
                old_key = 'conversation:' + item['pk']
                if old_key in self.library_checked:
                    self.library_checked.remove(old_key)
                    self.library_checked.add('conversation:' + target['pk'])
                self.handoff_state.set(f'已归入「{target["title"]}」，可右键查看各段续聊。')
                self.refresh()
            self.start('attach_continuation',{'pk':item['pk'],'target':target['pk']},done)
        self.button(panel,'确认归入',save,True).pack(anchor='e',pady=(12,0))

    def context_manage_continuation(self):
        item = self.context_item()
        parts = item.get('parts',[]) if item else []
        if len(parts) < 2 or self.busy:
            return
        window = tk.Toplevel(self.root)
        window.title('续聊记录 · '+item['title'])
        panel = ttk.Frame(window,padding=18)
        panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='按接续顺序显示各段。移出后，这段会单独出现在对话列表。').pack(anchor='w')
        selection = tk.Listbox(panel,width=65,height=min(10,len(parts)),exportselection=False)
        selection.pack(fill='both',expand=True,pady=10)
        for i,part in enumerate(parts,1):
            selection.insert('end',f'{i}. {part["title"]} · {part["source"]} · {part["messages"]} 条消息')
        def remove():
            picked = selection.curselection()
            if not picked or picked[0] == 0:
                messagebox.showinfo('移出续聊','请选择第二段或之后的一段。',parent=window)
                return
            window.destroy()
            self.start('detach_continuation',{'pk':parts[picked[0]]['pk']},lambda _:self.refresh())
        self.button(panel,'移出所选续聊',remove).pack(anchor='e')

    def context_files(self,folder=False):
        item=self.context_item()
        if not item:return
        title=item['title']
        if item['backup']:
            self.show_source_files(title,[{'path':p,'date':'','exists':Path(p).is_file()} for p in item['paths']],folder)
        else:
            self.start('conversation_files',{'pk':item['pk']},lambda files:self.show_source_files(title,files,folder))

    def show_source_files(self,title,files,folder=False):
        if not files:
            messagebox.showinfo('原始备份','未找到关联备份，请刷新列表或检查资料库。',parent=self.root);return
        window=tk.Toplevel(self.root);window.title('原始备份 · '+title);window.geometry('900x390')
        panel=ttk.Frame(window,padding=18);panel.pack(fill='both',expand=True)
        ttk.Label(panel,text='选择一份原始备份。多次采集的 ZIP 会分别列出；原文件不会被修改。').pack(anchor='w',pady=(0,10))
        tree=self.tree(panel,[('name','备份文件（完整路径）',630),('state','文件状态',100)])
        for index,item in enumerate(files):tree.insert('','end',iid=str(index),values=(item['path'],'可打开' if item['exists'] else '文件缺失'))
        tree.selection_set('0');tree.focus('0')
        def open_selected(as_folder):
            selected=tree.selection()
            if not selected:return
            path=Path(files[int(selected[0])]['path'])
            if not path.is_file():
                messagebox.showinfo('找不到文件','原始备份已移动或缺失：\n'+str(path),parent=window);return
            try:
                if as_folder:
                    import subprocess
                    subprocess.Popen(['explorer.exe','/select,',str(path)])
                else:os.startfile(str(path))
            except OSError as exc:messagebox.showerror('无法打开',str(exc),parent=window)
        bar=ttk.Frame(panel);bar.pack(side='bottom',fill='x',pady=(10,0),before=tree.master)
        self.button(bar,'打开所选 ZIP',lambda:open_selected(False)).pack(side='left')
        self.button(bar,'在文件夹中显示',lambda:open_selected(True)).pack(side='left',padx=8)
        tree.bind('<Double-1>',lambda event:open_selected(folder))
        if folder and len(files)==1:open_selected(True)

    def load_catalog(self,folders,show_backups=True):
        def loaded(result):
            self.library_conversations=result['conversations'];self.pending_backups=result['backups']
            self.trashed_conversations=result['trashed_conversations']
            if show_backups or (not self.library_conversations and self.pending_backups):self.library_view.set('从备份文件添加')
            self.render_library()
            self.handoff_state.set(f"资料库 {len(self.library_conversations)} 条对话；发现 {len(self.pending_backups)} 组备份（已自动扫描子目录、归并分卷）。"+(f"有 {len(result['scan_errors'])} 个包无法识别，详情见任务记录。" if result['scan_errors'] else ''))
            for item in result['scan_errors']:self.append_log('无法识别备份：'+item['path']+'；'+item['error'])
        self.start('catalog',{'folders':folders},loaded)

    def scan_backup_folder(self):
        folder=filedialog.askdirectory(title='选择最外层备份文件夹（自动递归查找全部 ZIP 和分卷）',parent=self.root)
        if folder:self.load_catalog([folder])

    def render_library(self):
        self.dismiss_library_context()
        self.library_tree.delete(*self.library_tree.get_children());self.library_rows={}
        backup=self.library_view.get()=='从备份文件添加'
        trashed=self.library_view.get()=='回收站'
        items=self.trashed_conversations if trashed else self.pending_backups if backup else self.library_conversations
        for item in items:
            key=('backup:'+item['id']) if backup else ('trash:' if trashed else 'conversation:')+item['pk']
            parts = item.get('parts',[])
            source = f"{len(item['paths'])} 个 ZIP" if backup else item['source']
            if len(parts) > 1:
                source = f"{len(parts)} 段续聊 · {len({part['source'] for part in parts})} 个账号标识"
            row=self.library_tree.insert('','end',iid=key,values=('✓' if key in self.library_checked else '',item['title'],
                source,item['status'] if backup else f"{item['messages']} 条消息",item['date'][:19]))
            self.library_rows[row]={'backup':backup,'trashed':trashed,**item}
        if self.library_rows:self.library_empty.place_forget()
        else:
            self.library_empty.configure(text='回收站为空' if trashed else '这里会收好你的对话\n\n先读取旧账号，或扫描已有备份文件夹')
            self.library_empty.place(relx=.5,rely=.52,anchor='center')

    def toggle_library_row(self,event):
        if self.library_tree.identify_column(event.x)!='#1':return
        row=self.library_tree.identify_row(event.y)
        if row:
            if row in self.library_checked:self.library_checked.remove(row)
            else:self.library_checked.add(row)
            self.library_tree.set(row,'chosen','✓' if row in self.library_checked else '')

    def toggle_library_all(self):
        rows=set(self.library_rows)
        if rows and rows.issubset(self.library_checked):self.library_checked-=rows
        else:self.library_checked|=rows
        self.render_library()

    def read_selected_backups(self):
        selected=[item for key,item in self.library_rows.items() if key in self.library_checked and item['backup']]
        if not selected:self.handoff_state.set('先切换到“从备份文件添加”，勾选要读取的记录；已有对话直接在资料库选择。');return
        if any(not item['complete'] for item in selected):self.handoff_state.set('所选备份存在缺卷，请扫描包含所有分卷的最外层文件夹。');return
        self.zip_paths=list(dict.fromkeys(path for item in selected for path in item['paths']))
        self.zip_path.set(f'已选 {len(self.zip_paths)} 个 ZIP（含同一对话分卷）')
        self.import_zip(False)

    def desktop_steps(self):
        self.show_document('新账号桌面接续步骤','1. 勾选要接续的旧对话，点击“本地分对话接续”。\n2. 点“打开项目资料目录”，在新账号桌面 Work 中建一个“旧账号对话”本地项目，关联这个总目录。\n3. 在同一个项目里，为每条旧对话新建一个聊天。\n4. 从列表选择对应旧对话，点“复制这条对话的接续指令”，粘贴到它的新聊天。指令会定位到自己的子目录。\n\nWork 按需要读取该对话的正文和图片。聊天由你创建，接续资料已按对话分好。')

    def build_search(self):
        bar = ttk.Frame(self.search_tab)
        bar.pack(fill="x", pady=(0, 12))
        ttk.Label(bar, text="关键词").pack(side="left")
        entry = ttk.Entry(bar, textvariable=self.query)
        entry.pack(side="left", fill="x", expand=True, padx=8)
        entry.bind("<Return>", lambda e: self.search())
        ttk.Label(bar, text="标题").pack(side="left")
        ttk.Entry(bar, textvariable=self.topic, width=18).pack(side="left", padx=8)
        self.button(bar, "搜索", self.search, True).pack(side="left")
        self.search_tree = self.tree(self.search_tab, [("title", "对话标题", 240), ("role", "角色", 90), ("text", "消息预览", 530)])
        self.search_tree.bind("<<TreeviewSelect>>", self.show_search)
        self.button(self.search_tab, "查看选中对话的全部版本与分支", self.load_conversation).pack(anchor="w", pady=8)
        self.search_detail = ScrolledText(self.search_tab, height=8, wrap="word", font=("Microsoft YaHei UI", 10))
        self.search_detail.pack(fill="both", expand=True)

    def build_memories(self):
        bar = ttk.Frame(self.memory_tab)
        bar.pack(fill="x", pady=(0, 10))
        self.button(bar, "提取全部历史记忆", self.extract, True).pack(side="left")
        ttk.Label(bar, text="状态").pack(side="left", padx=(20, 6))
        ttk.Combobox(bar, textvariable=self.status_filter, values=["全部", *STATUS.values()], state="readonly", width=12).pack(side="left")
        self.button(bar, "刷新记忆", self.load_memories).pack(side="left", padx=8)
        self.memory_tree = self.tree(self.memory_tab, [("status", "状态", 85), ("kind", "类别", 100), ("content", "记忆内容", 650)])
        self.memory_tree.bind("<<TreeviewSelect>>", self.show_memory)
        self.memory_text = ScrolledText(self.memory_tab, height=4, wrap="word", font=("Microsoft YaHei UI", 10))
        self.memory_text.pack(fill="both", expand=True, pady=(10, 4))
        self.memory_source = tk.StringVar(value="选择一条记忆查看证据；内容修改后会重新标记为待确认。")
        ttk.Label(self.memory_tab, textvariable=self.memory_source, wraplength=940, foreground="#777777").pack(anchor="w", pady=4)
        actions = ttk.Frame(self.memory_tab)
        actions.pack(fill="x", pady=5)
        for text, fn in [("确认有效", self.confirm_memory), ("保存修改", self.edit_memory), ("标记过时", self.outdate_memory),
                         ("查看历史 / 回滚", self.history_memory), ("软删除", self.delete_memory)]:
            self.button(actions, text, fn).pack(side="left", padx=(0, 8))

    def build_backup(self):
        frame = self.backup_tab
        ttk.Label(frame, text="把完整资料库带走", font=("Microsoft YaHei UI", 18, "bold")).pack(anchor="w")
        ttk.Label(frame, text="全量导出会包含原始 ZIP、附件、对话、全部记忆状态和数据库快照。", foreground="#777777").pack(anchor="w", pady=(6, 16))
        ttk.Label(frame,text='备份统一保存到数据位置下的“完整备份”，无需单独设置输出目录。').pack(anchor='w',pady=7)
        self.button(frame,'更改数据保存位置…',self.storage_settings).pack(anchor='w')
        actions = ttk.Frame(frame)
        actions.pack(fill="x", pady=12)
        self.button(actions, "备份当前资料库", self.export, True).pack(side="left")
        self.button(actions, "校验当前资料库", self.verify_library).pack(side="left", padx=8)
        self.button(actions, "检查已有资料包完整性", self.verify_package).pack(side="left")
        ttk.Separator(frame).pack(fill="x", pady=20)
        ttk.Label(frame, text="从迁移包恢复", font=("Microsoft YaHei UI", 14, "bold")).pack(anchor="w")
        ttk.Label(frame, text="选择包含 manifest.json 的文件夹，再选恢复位置。程序会建立一个新资料库，完成后自动切换。", wraplength=920).pack(anchor="w", pady=(8, 14))
        self.button(frame, "选择完整迁移包并恢复…", self.restore).pack(anchor="w")
        ttk.Separator(frame).pack(fill="x", pady=16)
        self.button(frame, "清空本地存档…", self.reset_library).pack(anchor="w")
        self.button(frame, '清理接续目录…', self.cleanup_workspaces).pack(anchor='w', pady=8)

    def cleanup_workspaces(self):
        def active():
            return self.busy or (self.browser_bridge and any(task['status'] in ('queued','dispatched','running')
                for task in self.browser_bridge.tasks.values()))
        if active():
            messagebox.showinfo('任务还在运行','请等待或中断当前上传，再清理接续资料。',parent=self.root)
            return
        root=self.output_directory('接续目录')
        def show(rows):
            window=tk.Toplevel(self.root);window.title('清理接续目录');window.geometry('900x560')
            panel=ttk.Frame(window,padding=20);panel.pack(fill='both',expand=True)
            ttk.Label(panel,text='清理生成的接续资料',font=('Microsoft YaHei UI',16,'bold')).pack(anchor='w')
            ttk.Label(panel,text='仅删除下列生成目录，资料库、原始备份和云端聊天保留。删除未完成目录后无法沿该记录续传；桌面 Work 也将无法读取已删除的目录。',wraplength=840).pack(anchor='w',pady=10)
            tree=self.tree(panel,[('name','目录',475),('size','占用',100),('status','状态',180)])
            tree.configure(selectmode='extended')
            by_id={}
            for row in rows:
                key=tree.insert('','end',values=(row['name'],f'{row["bytes"]/1024**2:.1f} MiB',row['status']));by_id[key]=row
            hint=tk.StringVar(value=f'共 {len(rows)} 个目录，占用 {sum(row["bytes"] for row in rows)/1024**3:.2f} GiB。按住 Ctrl 可多选。')
            ttk.Label(panel,textvariable=hint).pack(anchor='w',pady=8)
            def selection_changed(event=None):
                selected=[by_id[key] for key in tree.selection()]
                hint.set(f'已选 {len(selected)} 个目录，可释放 {sum(row["bytes"] for row in selected)/1024**2:.1f} MiB')
            tree.bind('<<TreeviewSelect>>',selection_changed)
            buttons=ttk.Frame(panel);buttons.pack(fill='x',pady=8)
            self.button(buttons,'选择已完成上传',lambda:tree.selection_set([key for key,row in by_id.items() if row['status']=='已完成上传'])).pack(side='left')
            self.button(buttons,'全选',lambda:tree.selection_set(list(by_id))).pack(side='left',padx=8)
            def remove():
                if active():
                    messagebox.showinfo('任务还在运行','请先停止当前任务。',parent=window);return
                selected=[by_id[key] for key in tree.selection()]
                if not selected:return
                if not messagebox.askyesno('永久删除所选接续资料',f'将删除 {len(selected)} 个生成目录，释放约 {sum(row["bytes"] for row in selected)/1024**2:.1f} MiB。\n\n目录内的上传文件、接续指令和续传报告都会删除。未完成上传将无法继续，桌面 Work 关联也会失效。\n\n原始资料库与备份保留，可重新生成。确定删除？',parent=window,default='no'):return
                window.destroy()
                def done(result):
                    if self.restore_report_path and not self.restore_report_path.is_file():self.restore_report_path=None
                    self.handoff_state.set(f'已清理 {result["removed"]} 个接续目录，释放 {result["bytes"]/1024**2:.1f} MiB。资料库和原始备份保留。')
                self.start('clean_workspaces',{'root':str(root),'names':[row['name'] for row in selected],'confirmed':True},done)
            self.button(buttons,'删除所选目录…',remove).pack(side='right')
        self.start('list_workspaces',{'root':str(root)},show)

    def reset_library(self):
        self.poll_browser()
        if self.busy or self.browser_ingest_queue or (self.browser_bridge and any(
            task['status'] in ('queued', 'dispatched', 'running') for task in self.browser_bridge.tasks.values()
        )):
            messagebox.showinfo('任务还在运行', '请等当前采集或入库结束后，再清空本地存档。', parent=self.root)
            return
        output = self.output_parent.get()
        if not messagebox.askyesno(
            '永久清空本地存档',
            f'资料库：{self.data.get()}\n\n'
            f'数据输出：{output}\n\n'
            '将永久删除资料库中的对话、附件、记忆和回收站记录，以及上述输出位置的浏览器备份、接续目录和完整备份。'
            '\n\n此操作无法撤销。确定清空？',
            parent=self.root, default='no',
        ):
            return

        def done(result):
            self.last_output = self.last_handoff = self.last_import = None
            self.pending_backups.clear()
            self.library_checked.clear()
            self.browser_files.clear()
            self.browser_tasks.clear()
            self.browser_sources.clear()
            self.browser_partial.clear()
            for row in self.browser_rows.values():
                self.browser_tree.set(row,'status','待选择')
            self.zip_paths.clear()
            self.zip_path.set('')
            self.search_rows.clear()
            self.memory_rows.clear()
            self.search_tree.delete(*self.search_tree.get_children())
            self.memory_tree.delete(*self.memory_tree.get_children())
            self.set_text(self.search_detail, '')
            self.set_text(self.memory_text, '')
            self.memory_source.set('请选择记忆。')
            self.library_view.set('已保存的对话')
            self.handoff_state.set('资料库已清空，可以重新采集。')
            self.cache_reset_at=result['reset_at']
            if self.browser_bridge:
                self.browser_bridge.cache_reset_at=self.cache_reset_at
            if result['failed']:
                messagebox.showwarning('部分文件未清除', '请关闭占用这些目录的程序后重试。\n\n'
                                       + '\n'.join(result['failed']), parent=self.root)
            else:
                messagebox.showinfo('已清空本地存档', f'已清除 {result["conversations"]} 条对话及其本地存档。', parent=self.root)
            self.refresh()

        self.start('reset_library', {'output': output, 'confirmed': True}, done)

    def empty_hint(self, tree, title, subtitle):
        hint=ttk.Label(tree,text=title+'\n\n'+subtitle,justify='center',foreground='#8fa6bd',background='#ffffff')
        hint.place(relx=.5,rely=.52,anchor='center')
        return hint

    def tree(self, parent, columns):
        holder = ttk.Frame(parent,style="Card.TFrame")
        holder.pack(fill="both", expand=True)
        tree = ttk.Treeview(holder, columns=[x[0] for x in columns], show="headings", height=5, selectmode="browse")
        for key, label, width in columns:
            tree.heading(key, text=label)
            tree.column(key, width=width, minwidth=65)
        scroll = ttk.Scrollbar(holder, orient="vertical", command=tree.yview)
        tree.configure(yscrollcommand=scroll.set)
        tree.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")
        return tree

    def set_text(self, widget, text):
        widget.delete("1.0", "end")
        widget.insert("1.0", text)

    def append_log(self, text):
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def start(self, operation, payload=None, callback=None):
        if self.busy:
            return
        self.busy = True
        data = self.data.get()
        for button in self.buttons:
            if button.winfo_exists():
                button.configure(state="disabled")
        self.spinner.start(12)
        self.state.set("正在处理，请保留窗口…")

        def worker():
            # Coalesce progress so large archives cannot flood the Tk event queue.
            import time
            last = [0.0]

            def progress(message):
                if time.monotonic() - last[0] > .15:
                    self.events.put(("progress", message, None))
                    last[0] = time.monotonic()
            try:
                result = run_operation(operation, data, payload or {}, progress)
                self.events.put(("done", result, callback))
            except Exception as exc:
                detail = str(exc) if isinstance(exc, BridgeError) else f"{type(exc).__name__}: {exc}"
                self.events.put(("error", detail, None))
        threading.Thread(target=worker, daemon=True).start()

    def poll(self):
        self.poll_browser()
        try:
            while True:
                kind, result, callback = self.events.get_nowait()
                if kind == "progress":
                    self.state.set(result)
                    continue
                self.busy = False
                self.spinner.stop()
                for button in self.buttons:
                    if button.winfo_exists():
                        button.configure(state="normal")
                if kind == "error":
                    self.state.set("本次任务未完成 · 请查看提示")
                    self.append_log("未完成：" + result)
                    messagebox.showerror("本次操作未完成", result + "\n\n已有原始归档会保留；未完成的导出目录不会当作完成包。", parent=self.root)
                else:
                    self.state.set("处理完成")
                    if callback:
                        callback(result)
        except Empty:
            pass
        if not self.busy and self.browser_ingest_queue:
            task,paths,source=self.browser_ingest_queue.pop(0)
            def ingested(result,task=task):
                self.import_done(result,notify=False)
                row=self.browser_tasks.get(task)
                if row and self.browser_tree.exists(row):
                    status=result['import']['status']
                    self.browser_tree.set(row,'status','部分备份已入库，可继续采集' if task in self.browser_partial and status!='rejected' else {'complete':'已入库','partial':'已入库，请看报告','rejected':'未入库，校验失败'}.get(status,status))
            self.start('import',{'zips':paths,'source':source,'capture_task':task},ingested)
        self.poll_id = self.root.after(80, self.poll)

    def choose_library(self):
        self.storage_settings()

    def apply_storage(self,data,output,copy_existing=False):
        def done(result):
            self.data.set(result['data_root']);self.output_parent.set(result['export_root'])
            self.storage_root.set(result.get('storage_root') or (str(Path(result['data_root']).parent) if Path(result['data_root']).name=='资料库' and Path(result['data_root']).parent==Path(result['export_root']) else result['data_root']))
            self.last_handoff=None;self.library_checked.clear();self.pending_backups=[]
            self.search_tree.delete(*self.search_tree.get_children());self.memory_tree.delete(*self.memory_tree.get_children())
            self.append_log('存储目录已保存。'+('现有资料已复制并校验，原目录保留。' if result['copied'] else '已切换到所选资料库。'))
            self.refresh()
        self.start('configure_storage',{'data_root':data,'export_root':output,'copy_existing':copy_existing,'settings_path':self.settings_path},done)

    def apply_storage_root(self,root,copy_existing=True):
        def done(result):
            self.data.set(result['data_root']);self.output_parent.set(result['export_root']);self.storage_root.set(result['storage_root'])
            self.last_handoff=None;self.library_checked.clear();self.pending_backups=[]
            self.search_tree.delete(*self.search_tree.get_children());self.memory_tree.delete(*self.memory_tree.get_children())
            self.append_log('统一数据位置已保存。'+('资料库已复制并校验，原件保留。' if result['copied'] else '已使用所选资料库。')+'已有输出文件仍保留原位置。')
            self.refresh()
        self.start('configure_root',{'storage_root':root,'copy_existing':copy_existing,'settings_path':self.settings_path},done)

    def output_directory(self,category):
        # Keep old split settings working until the user applies the unified location.
        root=Path(self.output_parent.get())
        return root/category if Path(self.data.get())==root/'资料库' else root

    def storage_settings(self):
        window=tk.Toplevel(self.root);window.title('存储设置');window.geometry('820x420')
        panel=ttk.Frame(window,padding=22);panel.pack(fill='both',expand=True)
        root=tk.StringVar(value=self.storage_root.get());copy=tk.BooleanVar(value=True)
        ttk.Label(panel,text='只选一个数据保存位置',font=('Microsoft YaHei UI',15,'bold')).pack(anchor='w',pady=(0,12))
        def choose(variable):
            selected=filedialog.askdirectory(title='选择保存目录',parent=window)
            if selected:variable.set(selected)
        self.path_row(panel,'数据保存位置',root,lambda:choose(root))
        ttk.Checkbutton(panel,text='复制现有资料库到新位置（原件保留）',variable=copy).pack(anchor='w',pady=12)
        ttk.Label(panel,text='软件会自动整理：\n资料库／  保存对话、索引和记忆\n浏览器备份／  保存采集的 ZIP\n接续目录／  保存给新账号的桌面资料\n完整备份／  保存整库备份\n\n复制时“资料库”子目录须为空；打开已有统一目录时取消勾选复制。\n已有浏览器备份和接续文件仍留在原处，不会自动搬动。',wraplength=740).pack(anchor='w',pady=5)
        def save():
            self.apply_storage_root(root.get(),copy.get());window.destroy()
        self.button(panel,'保存并应用',save,True).pack(anchor='w',pady=12)

    def choose_zip(self):
        paths = filedialog.askopenfilenames(title="选择 ZIP（同一对话的所有分卷可一起选）", filetypes=[("ZIP 文件", "*.zip"), ("所有文件", "*.*")], parent=self.root)
        if paths:
            self.zip_paths=list(paths)
            self.zip_path.set(paths[0] if len(paths)==1 else f'已选 {len(paths)} 个 ZIP（含同一对话分卷）')

    def browser_capture_helper(self):
        window = tk.Toplevel(self.root)
        window.title('浏览器采集 → 本地导入')
        window.geometry('820x650')
        panel = ttk.Frame(window, padding=24)
        panel.pack(fill='both', expand=True)
        text = ('不需要官方导出 ZIP，也不需要提供账号密码。\n\n'
                '① 更新到插件 1.4.0，在扩展管理页重新加载，再刷新 ChatGPT 网页。\n'
                '② 插件会自动连接本机软件，顶部显示连接状态；通常不用配对码。\n'
                '③ 点“读取对话列表”，勾选对话，再点“开始采集”。\n'
                '④ 采集自动入库；在“已保存的对话”勾选并点“本地分对话接续”。网页批量导入可作辅助。\n\n'
                '中断后可继续。未成功送达的旧片段会一并导出，不会只打包新增内容。\n'
                '自动采集暂停时也尝试保存部分 ZIP；有缺口会提示，不当作完整采集。\n'
                '已有插件和断点时：不要移除或换目录重装。只更新原目录并重新加载。\n'
                '导入会保留原始 ZIP、消息 HTML、版本、采集报告及包中实际存在的附件。\n'
                '断页、未完成采集或缺失附件会继续标记，不会被变成“完整恢复”。\n'
                '新账号接续依靠导出的资料和初始化提示词，不恢复官方侧边栏。')
        ttk.Label(panel, text=text, wraplength=720, justify='left').pack(anchor='w', pady=(0, 18))
        candidates = [Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[2])) / 'browser-extension',
                      Path(__file__).resolve().parents[2] / 'extension']
        extension = next((p for p in candidates if (p / 'manifest.json').is_file()), None)
        def open_extension():
            if extension and hasattr(os, 'startfile'):
                os.startfile(str(extension))
            else:
                messagebox.showinfo('插件目录', str(extension or '请使用交付目录中的“浏览器插件/extension”文件夹。'), parent=window)
        self.button(panel, '打开随软件提供的插件文件夹', open_extension).pack(anchor='w', pady=5)
        def update_extension():
            if extension is None:return
            target=filedialog.askdirectory(title='选择原来已安装的 Memory Bridge 插件文件夹（包含 manifest.json）',parent=window)
            if not target:return
            try:
                from .plugin_update import update_plugin
                report=update_plugin(extension,target,self.output_directory('完整备份'))
                messagebox.showinfo('插件文件已更新','已保留旧文件备份。请到浏览器扩展管理页点击此插件的“重新加载”，再刷新 GPT 网页。\n\n备份：'+report['backup'],parent=window)
            except (OSError,ValueError,BridgeError) as exc:messagebox.showerror('未完成更新',str(exc),parent=window)
        self.button(panel,'更新原来已安装的插件…',update_extension).pack(anchor='w',pady=5)
        self.button(panel, '打开 Edge 扩展管理页', lambda: webbrowser.open('edge://extensions', new=2)).pack(anchor='w', pady=5)
        def choose_capture():
            window.destroy()
            self.go_to_import(False)
            self.scan_backup_folder()
        self.button(panel, '我已导出插件包，扫描文件夹…', choose_capture, True).pack(anchor='w', pady=10)

    def open_official_page(self, url):
        if not webbrowser.open(url, new=2):
            messagebox.showerror("浏览器未打开", "请在浏览器中打开：\n" + url, parent=self.root)
            return False
        self.append_log("已打开官方页面（尚未提交申请）：" + url)
        return True

    def official_export_helper(self):
        window = tk.Toplevel(self.root)
        window.title("官方数据导出助手")
        window.geometry("790x550")
        window.minsize(700, 500)
        panel = ttk.Frame(window, padding=24)
        panel.pack(fill="both", expand=True)
        ttk.Label(panel, text="先向 ChatGPT 申请，再接入迁移", font=("Microsoft YaHei UI", 17, "bold")).pack(anchor="w", pady=(0, 14))
        text = (
            "① 打开 ChatGPT，登录你要迁移的旧账号。\n"
            "② 在 设置 → 数据控制 → 导出数据 中确认申请；也可使用官方隐私门户。\n"
            "③ 等待官方邮件或短信，打开其中的下载入口，保存 ZIP。\n"
            "④ 导出完成后，切换到新账号，在本软件读取备份并准备上传资料。\n\n"
            "官方导出是异步任务：官方说明最多可能需要7天，下载链接通常在收到后24小时过期。"
            "部分受管理的工作区不提供自助导出，以账号界面为准。\n\n"
            "这里的按钮仅打开官方页面；登录、账号验证与确认申请仍在官方页面完成。"
            "本软件没有官方导出请求状态或通知访问权限，不会声称已经替你申请或下载。"
        )
        ttk.Label(panel, text=text, wraplength=715, justify="left").pack(anchor="w", fill="x")
        actions = ttk.Frame(panel)
        actions.pack(fill="x", pady=18)
        self.button(actions, "打开 ChatGPT", lambda: self.open_official_page("https://chatgpt.com/"), True).pack(side="left")
        self.button(actions, "官方隐私门户", lambda: self.open_official_page("https://privacy.openai.com/")).pack(side="left", padx=8)
        self.button(actions, "官方导出说明", lambda: self.open_official_page("https://help.openai.com/en/articles/7260999-exporting-your-chatgpt-history-and-data")).pack(side="left")

        def downloaded():
            window.destroy()
            self.go_to_import(False)
            self.scan_backup_folder()
        self.button(panel, "我已下载，查找已有备份…", downloaded).pack(anchor="w")

    def choose_output(self):
        self.storage_settings()

    def refresh(self):
        def loaded(result):
            c = result["counts"]
            self.cache_reset_at=result.get('reset_at')
            if self.browser_bridge:
                self.browser_bridge.cache_reset_at=self.cache_reset_at
            self.library_conversations=result.get('library_conversations',[])
            self.trashed_conversations=result.get('trashed_conversations',[])
            messages=sum(row['messages'] for row in self.library_conversations)
            self.counts.set(f"{c['imports']} 份原始归档    ·    {len(self.library_conversations)} 个对话    ·    {messages} 个消息节点    ·    {c['memories']} 条记忆")
            self.render_library()
        self.start("overview", callback=loaded)

    def import_zip(self, migrate):
        paths=self.zip_paths if self.zip_path.get().startswith('已选 ') else [self.zip_path.get()]
        if not paths or any(not Path(p).is_file() for p in paths):
            messagebox.showinfo("先选一个 ZIP", "请选择旧账号导出的 ZIP；同一对话的多个分卷请一起选中。", parent=self.root)
            return
        if not self.source.get().strip():
            messagebox.showinfo("填写账号标识", "例如 old-account；同账号以后保持一致。", parent=self.root)
            return
        if migrate and not self.output_parent.get().strip():
            messagebox.showinfo("选择结果位置", "请先选择迁移结果的父文件夹。", parent=self.root)
            return
        payload = {"zip": paths[0], "zips":paths, "source": self.source.get().strip(), "retry": self.retry.get()}
        if migrate:
            payload["destination"] = unique_destination(self.output_parent.get())
        self.start("migrate" if migrate else "import", payload, self.import_done)

    def import_done(self, result,notify=True):
        entry = result["import"]
        if len(result.get('imports',[]))>1:
            self.append_log(f"本轮处理 {len(result['imports'])} 份逻辑备份；同账号、同对话 ID 的消息保存在同一条对话中。")
            for item in result['imports']:
                if item['status']!='complete':self.append_log(f"备份读取 #{item['id']}：{item['status']}，请核对报告。")
        self.last_import = entry
        linked = sum(item['stats'].get('linked_continuations',0) for item in result.get('imports',[entry]))
        if linked:
            self.append_log(f'按接续编号归入已有对话：{linked} 段。')
        status = entry["status"]
        self.append_log(f"备份读取 #{entry['id']}：{status}；本包解析对话 {entry['stats'].get('parsed_records', 0)}；警告 {len(entry['warnings'])} 项。")
        coverage = entry['stats'].get('browser_coverage')
        if coverage:
            self.append_log(f"浏览器采集：{coverage['messages']} 条消息，{coverage['segments']} 个片段；页面检查{'通过' if coverage['page_checks_passed'] else '未完成／有缺口'}；附件已保存 {coverage['saved_attachments']}，未取得 {coverage['unavailable_attachments']}。范围为当前显示分支。")
        if result.get("destination"):
            self.export_done(result, refresh=False)
        else:
            self.last_handoff=None
            self.library_view.set('已保存的对话')
            message = f'已保存备份，并将 {linked} 段续聊归入已有对话。' if linked else '已保存备份。勾选对话即可生成上传资料。'
            self.handoff_state.set(message)
        if notify and (status != "complete" or any(item['status']!='complete' for item in result.get('imports',[]))):
            messagebox.showwarning("有内容需要留意", "读取状态为 " + status + "。原始 ZIP 已归档；请查看读取报告中的警告，尚不能视为完整解析。", parent=self.root)
        self.refresh()

    def show_import_report(self):
        if not self.last_import:
            messagebox.showinfo('备份读取报告', '请先导入一份插件包或官方 ZIP。', parent=self.root)
            return
        entry = self.last_import
        lines = ['来源：' + ('浏览器插件采集包' if entry['stats'].get('input_kind') == 'browser_extension' else '官方／其他 ZIP'),
                 '读取状态：' + entry['status'],
                 '原始包已独立归档并计算校验值。校验与输入一致，不等于账号全部内容已取得。', '']
        coverage = entry['stats'].get('browser_coverage')
        if coverage:
            lines += [f"已采集 {coverage['messages']} 条消息；{coverage['segments']} 个页面片段。",
                      '页面首尾与衔接：' + ('检查通过' if coverage['page_checks_passed'] else '未完成或仍有缺口'),
                      f"附件：已保存 {coverage['saved_attachments']}；未取得 {coverage['unavailable_attachments']}。",
                      '范围：当前显示分支；不恢复原账号侧边栏、官方记忆和账号状态。', '']
        lines += ['详细索引与警告：', json.dumps(entry, ensure_ascii=False, indent=2)]
        self.show_document('备份读取报告', '\n'.join(lines))

    def prepare_handoff(self):
        if not self.output_parent.get().strip():return
        selected=[item['pk'] for key,item in self.library_rows.items() if key in self.library_checked and not item['backup'] and not item.get('trashed')]
        if not selected:self.handoff_state.set('请在“已保存的对话”列表勾选要交给新账号的对话，不需要再选择 ZIP。');return
        operation='desktop_workspace' if len(selected)==1 else 'prepare_batch_restore'
        self.start(operation,{'destination':unique_destination(self.output_directory('接续目录'),'桌面接续目录'),'selected':selected},self.handoff_done)

    def restore_selected(self):
        if not self.require_browser() or self.busy:return
        if any(task['status'] in ('queued','dispatched','running') for task in self.browser_bridge.tasks.values()):
            self.handoff_state.set('请等当前浏览器任务结束后再导入。');return
        selected=[item['pk'] for key,item in self.library_rows.items() if key in self.library_checked
                  and not item['backup'] and not item.get('trashed')]
        if not selected:
            self.handoff_state.set('请先勾选要导入新账号的对话。');return
        if not messagebox.askyesno('导入当前浏览器账号',
            f'本批统一使用网页 Work。将在浏览器当前登录的 ChatGPT 账号中分别新建 {len(selected)} 个 Work 聊天，上传对应正文、附件并发送各自的接续提示词。'
            '\n\n请先在浏览器确认已登录目标新账号。网页文件额度和支持的格式会影响上传，失败时本轮暂停。\n\n现在开始？',
            parent=self.root,default='no'):return
        def ready(result):
            self.handoff_done(result)
            if self.restore_cancel_requested:
                self.handoff_state.set('已取消开始上传，生成的本地资料已保留。');return
            self.restore_tasks.clear()
            self.restore_history=[]
            self.restore_report_path=Path(result['destination'])/'导入结果.json'
            for job in result['jobs']:
                task=self.browser_bridge.submit('restore',target_mode='work',**job)
                self.restore_tasks[task]={'title':job['title'],'pk':job['pk'],'mode':'work','status':'queued','chat_url':''}
            self.handoff_state.set(f'已排队 {len(result["jobs"])} 条对话，插件将逐条切到网页 Work 并上传。请保持浏览器与软件打开。')
        self.restore_cancel_requested = False
        self.start('prepare_batch_restore',{'destination':unique_destination(self.output_directory('接续目录'),'桌面接续目录'),
                                           'selected':selected},ready)

    def resume_upload(self):
        if not self.require_browser() or self.busy:return
        if any(task['status'] in ('queued','dispatched','running') for task in self.browser_bridge.tasks.values()):
            self.handoff_state.set('请先中断当前上传，再继续未完成记录。');return
        from .batch_restore import choose_resume_report
        selected=[item['pk'] for key,item in self.library_rows.items() if key in self.library_checked and not item['backup']]
        report=choose_resume_report(self.output_directory('接续目录'),selected,self.restore_report_path)
        if not report or not report.is_file():
            chosen=filedialog.askopenfilename(title='选择上次的导入结果.json',initialdir=self.output_directory('接续目录'),
                                               filetypes=[('导入结果','导入结果.json')],parent=self.root)
            if not chosen:return
            report=Path(chosen)
        selected=[item['pk'] for key,item in self.library_rows.items() if key in self.library_checked and not item['backup']]
        def ready(result):
            if not messagebox.askyesno('在原聊天继续上传',
                f'将继续 {len(result["jobs"])} 条未完成记录。\n'+
                '\n'.join(f'「{job["title"]}」已发送 {job["resume_sent_files"]} 个文件；'+('返回原聊天' if job['resume_url'] else '尚无原聊天，将新建') for job in result['jobs'])+
                '\n请确认浏览器仍登录同一个目标账号。' ,
                parent=self.root,default='no'):return
            self.last_handoff=Path(result['destination'])
            self.restore_report_path=self.last_handoff/'导入结果.json'
            wanted={job['pk'] for job in result['jobs']}
            self.restore_history=[row for row in result['records'] if row['pk'] not in wanted]
            previous={row['pk']:row for row in result['records']}
            self.restore_tasks.clear()
            for job in result['jobs']:
                task=self.browser_bridge.submit('restore',target_mode='work',**job)
                self.restore_tasks[task]={**previous.get(job['pk'],{}),'pk':job['pk'],'title':job['title'],'status':'queued'}
            self.restore_cancel_requested=False
            self.handoff_state.set('已沿原聊天排队续传，已确认发送的文件会跳过。')
        self.start('resume_batch_restore',{'report':str(report),'selected':selected},ready)

    def local_selected(self):
        if self.busy:return
        selected=[item['pk'] for key,item in self.library_rows.items() if key in self.library_checked
                  and not item['backup'] and not item.get('trashed')]
        if not selected:
            self.handoff_state.set('请先勾选要在新账号接续的对话。');return
        def ready(result):
            self.handoff_done(result)
            window=tk.Toplevel(self.root)
            window.title('本地分对话接续')
            window.geometry('920x540')
            panel=ttk.Frame(window,padding=20);panel.pack(fill='both',expand=True)
            ttk.Label(panel,text='一个本地项目，多条独立聊天',font=('Microsoft YaHei UI',15,'bold')).pack(anchor='w')
            ttk.Label(panel,text='先打开项目资料目录，在新账号桌面 Work 建一个“旧账号对话”本地项目并关联该目录。之后在同一项目里分别新建聊天，粘贴所选对话的接续指令。',
                      wraplength=850).pack(anchor='w',pady=12)
            tree=self.tree(panel,[('title','对话',380),('folder','独立资料目录',430)])
            jobs={}
            for job in result['jobs']:
                row=tree.insert('','end',values=(job['title'],job['directory']))
                jobs[row]=job
            first=tree.get_children()[0];tree.selection_set(first);tree.focus(first)
            actions=ttk.Frame(panel);actions.pack(fill='x',pady=12)
            def chosen():
                selection=tree.selection()
                return jobs[selection[0]] if selection else None
            def open_folder():
                job=chosen()
                if job:os.startfile(job['directory'])
            def copy_prompt():
                job=chosen()
                if job:
                    prompt=(Path(job['directory'])/'桌面接续指令.txt').read_text(encoding='utf-8')
                    self.root.clipboard_clear();self.root.clipboard_append(prompt)
                    self.handoff_state.set('已复制「'+job['title']+'」的独立接续指令。')
            ttk.Button(actions,text='打开项目资料目录',command=lambda:os.startfile(result['destination'])).pack(side='left')
            ttk.Button(actions,text='查看所选对话文件',command=open_folder).pack(side='left',padx=8)
            ttk.Button(actions,text='复制这条对话的接续指令',command=copy_prompt).pack(side='left',padx=8)
            ttk.Label(panel,text='每条接续指令定位到自己的子目录。跨设备继续需账号支持 Work Cloud 与本地电脑访问，并新建云端任务；读取这些文件时电脑须在线。',
                      wraplength=850,foreground='#6b6b6b').pack(anchor='w')
        self.start('prepare_batch_restore',{'destination':unique_destination(self.output_directory('接续目录'),'桌面接续目录'),
                                           'selected':selected},ready)

    def open_account_page(self):
        if webbrowser.open('https://chatgpt.com/',new=2):
            self.handoff_state.set('网页辅助：切换到新账号后，手动上传工作目录中需要的 Markdown 分片和图片；网页不能直接读取本机整个资料库。')
        else:
            messagebox.showinfo('打开新账号','请在浏览器打开 https://chatgpt.com/，自行切换到新账号。',parent=self.root)

    def handoff_done(self,result):
        self.last_handoff=Path(result['destination'])
        self.last_output=self.last_handoff
        if 'jobs' in result:
            self.handoff_state.set(f'已为 {len(result["jobs"])} 条对话分别生成资料，每个子文件夹有独立提示词。')
            self.state.set('分对话资料已生成。')
            return
        self.handoff_state.set(f"已生成 {result.get('conversations',0)} 条对话的资料。打开资料文件夹，上传对话目录、正文和所需图片，再发送网页提示词。")
        self.append_log('桌面工作目录已准备并校验，未创建新账号会话或上传资料：\n'+str(self.last_handoff))
        self.state.set('资料已生成，等待你在新账号网页手动上传。桌面用法见“更多”。')

    def open_handoff(self):
        path=self.last_handoff
        if not path:
            messagebox.showinfo('先选择已保存的对话','在资料库列表勾选对话，点击“生成上传资料”。',parent=self.root);return
        os.startfile(str(path))

    def copy_web_prompt(self):
        if not self.last_handoff:
            self.handoff_state.set('请先勾选对话并点击“生成上传资料”，再复制网页提示词。');return
        from .desktop_workspace import web_prompt
        prompt_file = self.last_handoff/'网页接续提示词.txt'
        if not prompt_file.is_file():
            selected=filedialog.askopenfilename(title='选择这条对话的网页接续提示词',initialdir=self.last_handoff,
                                                filetypes=[('接续提示词','*.txt')],parent=self.root)
            if not selected:return
            prompt_file=Path(selected)
        prompt = prompt_file.read_text(encoding='utf-8') if prompt_file.is_file() else web_prompt()
        self.root.clipboard_clear();self.root.clipboard_append(prompt)
        self.handoff_state.set('网页提示词已复制。请在新账号上传资料后粘贴发送，保留首行接续编号（如有）。')

    def copy_handoff_prompt(self):
        if not self.last_handoff:self.handoff_state.set('先从资料库勾选对话并准备桌面目录，再复制接续指令。');return
        from .desktop_workspace import desktop_prompt
        prompt_file = self.last_handoff/'桌面接续指令.txt'
        if not prompt_file.is_file():
            selected=filedialog.askopenfilename(title='选择这条对话的桌面接续指令',initialdir=self.last_handoff,
                                                filetypes=[('接续指令','*.txt')],parent=self.root)
            if not selected:return
            prompt_file=Path(selected)
        prompt = prompt_file.read_text(encoding='utf-8') if prompt_file.is_file() else desktop_prompt(self.last_handoff)
        self.root.clipboard_clear();self.root.clipboard_append(prompt)
        self.handoff_state.set('桌面接续指令已复制，包含工作目录路径。请在新账号的 Work 本地任务中选用此目录并粘贴发送。')

    def search(self):
        self.start("search", {"query": self.query.get(), "topic": self.topic.get()}, self.search_loaded)

    def search_loaded(self, rows):
        self.search_tree.delete(*self.search_tree.get_children())
        self.search_rows.clear()
        for i, row in enumerate(rows):
            key = str(i)
            self.search_rows[key] = row
            self.search_tree.insert("", "end", iid=key, values=(row["title"], row["role"], row["text"].replace("\n", " ")[:150]))
        self.state.set(f"找到 {len(rows)} 条消息（本次最多显示 500 条）")
        self.set_text(self.search_detail, "选择消息查看正文与来源；可打开完整对话的所有版本。")

    def show_search(self, event=None):
        selected = self.search_tree.selection()
        if selected and selected[0] in self.search_rows:
            row = self.search_rows[selected[0]]
            self.set_text(self.search_detail, f"对话：{row['title']}\n账号：{row['source']}\n对话 ID：{row['conversation_id']}\n消息 ID：{row['message_id']}\n\n{row['text']}")

    def load_conversation(self):
        selected = self.search_tree.selection()
        if not selected:
            return
        row = self.search_rows[selected[0]]
        self.start("conversation", {"id": row["conversation_id"], "source": row["source"]},
                   lambda records: self.show_document("完整对话 · 所有原始版本与分支", json.dumps(records, ensure_ascii=False, indent=2)))

    def extract(self):
        def done(result):
            self.append_log(f"提取完成：新增 {result['candidates']} 条，已有 {result['duplicates']} 条。")
            self.load_memories()
        self.start("extract", callback=done)

    def load_memories(self):
        inverse = {value: key for key, value in STATUS.items()}
        self.start("memories", {"status": inverse.get(self.status_filter.get())}, self.memories_loaded)

    def memories_loaded(self, rows):
        self.memory_tree.delete(*self.memory_tree.get_children())
        self.memory_rows = {row["id"]: row for row in rows}
        for row in rows:
            self.memory_tree.insert("", "end", iid=row["id"], values=(STATUS.get(row["status"], row["status"]), row["kind"], row["content"].replace("\n", " ")[:150]))
        self.state.set(f"当前显示 {len(rows)} 条记忆")
        self.set_text(self.memory_text, "")
        self.memory_source.set("选择记忆可查看固定到原消息版本的证据。")

    def selected_memory(self):
        selected = self.memory_tree.selection()
        return self.memory_rows.get(selected[0]) if selected else None

    def show_memory(self, event=None):
        item = self.selected_memory()
        if item:
            self.set_text(self.memory_text, item["content"])
            sources = "；".join(f"{x['source']} / {x['conversation_id']} / {x['message_id']}" for x in item["sources"])
            self.memory_source.set(f"{item['epistemic']} · 已确认：{item['confirmed']} · 证据：{sources}")

    def update_memory(self, **fields):
        item = self.selected_memory()
        if item:
            self.start("memory_update", {"id": item["id"], **fields}, lambda result: self.load_memories())

    def confirm_memory(self):
        self.update_memory(status="active", confirmed=True)

    def edit_memory(self):
        self.update_memory(content=self.memory_text.get("1.0", "end-1c"))

    def outdate_memory(self):
        self.update_memory(status="outdated", confirmed=False)

    def delete_memory(self):
        item = self.selected_memory()
        if item and messagebox.askyesno("确认软删除", "这条记忆会标记为已删除，原始证据与历史仍保留。继续吗？", parent=self.root):
            self.start("memory_delete", {"id": item["id"], "confirmed": True}, lambda result: self.load_memories())

    def history_memory(self):
        item = self.selected_memory()
        if item:
            mid = item["id"]
            self.start("memory_history", {"id": mid}, lambda rows: self.history_window(mid, rows))

    def history_window(self, mid, rows):
        window = self.show_document("记忆历史 · 选择版本后回滚", json.dumps(rows, ensure_ascii=False, indent=2))
        bar = ttk.Frame(window, padding=12)
        bar.pack(fill="x", before=window.winfo_children()[0])
        version = tk.StringVar(value=str(rows[-1]["version"]) if rows else "")
        ttk.Combobox(bar, textvariable=version, values=[str(x["version"]) for x in rows], state="readonly", width=8).pack(side="left")

        def rollback():
            if version.get() and messagebox.askyesno("确认回滚", f"将该记忆恢复到版本 {version.get()}？现有修改仍会保留在历史中。", parent=window):
                window.destroy()
                self.start("memory_rollback", {"id": mid, "version": int(version.get())}, lambda result: self.load_memories())
        self.button(bar, "回滚到该版本", rollback).pack(side="left", padx=8)
        window.protocol("WM_DELETE_WINDOW", window.destroy)

    def show_document(self, title, text):
        window = tk.Toplevel(self.root)
        window.title(title)
        window.geometry("920x660")
        editor = ScrolledText(window, wrap="word", font=("Microsoft YaHei UI", 10), padx=16, pady=16)
        editor.pack(fill="both", expand=True)
        editor.insert("1.0", text)
        editor.configure(state="disabled")
        return window

    def export(self):
        if self.output_parent.get().strip():
            self.start("export", {"destination": unique_destination(self.output_directory('完整备份'))}, self.export_done)

    def export_done(self, result, refresh=True):
        self.last_output = Path(result["destination"])
        self.append_log(f"本地资料库备份已生成，{result['files']} 个文件校验通过：\n{self.last_output}")
        self.state.set("本地备份已生成并校验 · 未上传到账号")
        if refresh:
            self.refresh()

    def open_output(self):
        path = self.last_output or Path(self.output_parent.get())
        path.mkdir(parents=True, exist_ok=True)
        os.startfile(str(path))

    def verify_library(self):
        self.start("verify_library", callback=self.show_verification)

    def verify_package(self):
        path = filedialog.askdirectory(title="选择包含 manifest.json 的迁移包文件夹", parent=self.root)
        if path:
            self.start("verify_package", {"package": path}, self.show_verification)

    def show_verification(self, result):
        self.append_log("校验结果：" + ("通过" if result["ok"] else "未通过，请查看详情"))
        self.show_document("校验通过" if result["ok"] else "校验未通过", json.dumps(result, ensure_ascii=False, indent=2))

    def restore(self):
        package = filedialog.askdirectory(title="选择完整迁移包文件夹", parent=self.root)
        if not package:
            return
        parent = filedialog.askdirectory(title="选择恢复资料库的父文件夹（会建立新目录）", parent=self.root)
        if not parent:
            return
        recovered_root=unique_destination(parent,'恢复数据')
        destination = recovered_root/'资料库'

        def restored(result):
            self.data.set(str(destination))
            self.output_parent.set(str(recovered_root));self.storage_root.set(str(recovered_root))
            self.last_handoff=None;self.library_checked.clear();self.pending_backups=[]
            self.search_tree.delete(*self.search_tree.get_children())
            self.memory_tree.delete(*self.memory_tree.get_children())
            self.append_log("资料库已恢复并切换到：" + str(destination))
            self.refresh()
        self.start("restore", {"package": package, "destination": destination,'storage_root':recovered_root,'settings_path':self.settings_path,'export_root':recovered_root}, restored)

    def close(self):
        if self.browser_bridge and any(t['status'] in ('queued','dispatched','running') for t in self.browser_bridge.tasks.values()):
            messagebox.showinfo('浏览器任务还在运行','请等浏览器采集和传送完成后再关闭。',parent=self.root);return
        if self.busy or self.browser_ingest_queue:
            messagebox.showinfo("任务还在运行", "请等当前操作完成后再关闭，避免留下未完成的导出。", parent=self.root)
            return
        if self.browser_bridge:self.browser_bridge.close()
        self.root.after_cancel(self.poll_id)
        self.root.destroy()


def main(data_root="data", export_root="outputs/迁移结果",settings_path=None):
    if settings_path is None:
        from .settings import load,user_settings_path
        settings_path=user_settings_path();preferences=load(settings_path)
        if data_root=='data':data_root=preferences.get('data_root',data_root)
        if export_root=='outputs/迁移结果':export_root=preferences.get('export_root',export_root)
    root = tk.Tk()
    try:
        root.tk.call("tk", "scaling", root.winfo_fpixels("1i") / 72)
    except tk.TclError:
        pass
    BridgeApp(root, data_root=data_root, export_root=export_root,settings_path=settings_path,auto_connect=True)
    root.mainloop()


if __name__ == "__main__":
    main()
