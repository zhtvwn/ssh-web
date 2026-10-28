/* Web 终端：多标签并发连接多台主机（xterm.js + SocketIO） */

(function () {
    var hosts = window.APP_HOSTS || [];
    var snippets = window.APP_SNIPPETS || [];
    var initHostId = window.APP_INIT_HOST || null;

    var socket = null;
    var sessions = {};       // sessionId -> session 对象
    var order = [];          // 标签顺序
    var activeId = null;
    var seq = 0;

    // 外部模块（如文件面板）订阅：激活标签变化、标签关闭
    var activateHooks = [];
    var closeHooks = [];

    // workspace 标识浏览器标签（存在 sessionStorage：切页保留、关闭标签即失效）
    var workspace = sessionStorage.getItem('sshWorkspace');
    if (!workspace) {
        workspace = 'w' + Date.now().toString(36) + Math.random().toString(36).slice(2, 8);
        sessionStorage.setItem('sshWorkspace', workspace);
    }

    function persistTabs() {
        try {
            sessionStorage.setItem('sshTabs', JSON.stringify(order.map(function (id) {
                return {id: id, hostId: sessions[id].hostId, name: sessions[id].host.name};
            })));
        } catch (e) { /* 忽略存储失败 */ }
    }

    var termStack = document.getElementById('termStack');
    var termEmpty = document.getElementById('termEmpty');
    var tabList = document.getElementById('tabList');
    var hostSelect = document.getElementById('hostSelect');
    var statusEl = document.getElementById('status');
    var statusText = document.getElementById('status-text');

    var execPanel = document.getElementById('execPanel');
    var execOutput = document.getElementById('execOutput');
    var execTitle = document.getElementById('execTitle');

    /* --------------------------- 输入提示补全 --------------------------- */

    var suggestEl = null;      // 候选浮层
    var suggestItems = [];
    var suggestIndex = 0;
    var suggestVisible = false;
    var suggestSession = null;
    var history = [];
    var autoSuggest = localStorage.getItem('sshAutoSuggest') !== '0';

    function ensureSuggestEl() {
        if (suggestEl) return suggestEl;
        suggestEl = document.createElement('div');
        suggestEl.className = 'suggest-panel';
        suggestEl.style.display = 'none';
        termStack.appendChild(suggestEl);
        return suggestEl;
    }

    function pushHistory(cmd) {
        cmd = (cmd || '').trim();
        if (!cmd) return;
        history = [cmd].concat(history.filter(function (c) { return c !== cmd; })).slice(0, 100);
        try {
            localStorage.setItem('sshCmdHistory', JSON.stringify(history.slice(0, 50)));
        } catch (e) { /* 忽略存储失败 */ }
    }

    function loadHistory() {
        try {
            var cached = JSON.parse(localStorage.getItem('sshCmdHistory') || '[]');
            history = cached.concat(history);
        } catch (e) { /* 忽略 */ }
        fetch('/api/history').then(function (r) { return r.json(); }).then(function (list) {
            (list || []).forEach(pushHistory);
        }).catch(function () { /* 忽略 */ });
    }

    function buildCandidates(prefix) {
        prefix = (prefix || '').replace(/^\s+/, '');
        if (!prefix) return [];
        var lower = prefix.toLowerCase();
        var out = [];
        var seen = {};

        function add(cmd, name, type) {
            if (!cmd || seen[cmd]) return;
            seen[cmd] = true;
            out.push({command: cmd, name: name || '', type: type});
        }

        // 1) 命令前缀匹配（命令库）
        snippets.forEach(function (item) {
            if (item.command.toLowerCase().indexOf(lower) === 0) add(item.command, item.name, 'snippet');
        });
        // 2) 历史命令前缀匹配
        history.forEach(function (cmd) {
            if (cmd.toLowerCase().indexOf(lower) === 0) add(cmd, '', 'history');
        });
        // 3) 名称匹配 / 包含匹配
        snippets.forEach(function (item) {
            if (item.name.toLowerCase().indexOf(lower) >= 0) add(item.command, item.name, 'snippet');
        });
        snippets.forEach(function (item) {
            if (item.command.toLowerCase().indexOf(lower) > 0) add(item.command, item.name, 'snippet');
        });
        return out.slice(0, 10);
    }

    function positionSuggest(s) {
        var el = ensureSuggestEl();
        var pane = s.pane;
        var screen = s.term.element ? s.term.element.querySelector('.xterm-screen') : null;
        var cellW = screen && screen.clientWidth ? screen.clientWidth / s.term.cols : 8;
        var cellH = screen && screen.clientHeight ? screen.clientHeight / s.term.rows : 18;
        var buf = s.term.buffer.active;
        var left = 12 + buf.cursorX * cellW;
        var top = 12 + (buf.cursorY + 1) * cellH;
        var height = el.offsetHeight || 160;
        // 空间不足时向上翻转
        if (top + height > pane.clientHeight - 8) {
            top = Math.max(8, buf.cursorY * cellH - height - 6);
        }
        if (left + el.offsetWidth > pane.clientWidth - 8) {
            left = Math.max(8, pane.clientWidth - el.offsetWidth - 8);
        }
        el.style.left = Math.max(8, left) + 'px';
        el.style.top = Math.max(8, top) + 'px';
    }

    function renderSuggest(s) {
        var el = ensureSuggestEl();
        el.innerHTML = '';
        suggestItems.forEach(function (item, index) {
            var row = document.createElement('div');
            row.className = 'suggest-item' + (index === suggestIndex ? ' selected' : '');
            row.innerHTML =
                '<span class="suggest-cmd"></span>' +
                '<span class="suggest-tag">' + (item.type === 'history' ? '历史' : (item.name || '命令库')) + '</span>';
            row.querySelector('.suggest-cmd').textContent = item.command;
            row.addEventListener('click', function () {
                acceptSuggest(s, item);
            });
            el.appendChild(row);
        });
        positionSuggest(s);
        el.style.display = 'block';
        suggestVisible = true;
        suggestSession = s;
    }

    function hideSuggest() {
        if (suggestEl) suggestEl.style.display = 'none';
        suggestVisible = false;
        suggestItems = [];
        suggestIndex = 0;
        suggestSession = null;
    }

    function refreshSuggest(s, force) {
        if (!autoSuggest || !s || !s.connected) return hideSuggest();
        var prefix = s.buffer;
        if (prefix.trim().length < (force ? 1 : 2)) return hideSuggest();
        var items = buildCandidates(prefix);
        if (!items.length) return hideSuggest();
        suggestItems = items;
        suggestIndex = 0;
        renderSuggest(s);
    }

    function acceptSuggest(s, item) {
        if (!item) return;
        var data;
        if (item.command.indexOf(s.buffer) === 0) {
            data = item.command.slice(s.buffer.length);
        } else {
            // 非前缀匹配：先退格删掉已输入内容，再整条写入
            data = new Array(s.buffer.length + 1).join('\x7f') + item.command;
        }
        s.buffer = item.command;
        emit('ssh_input', {session_id: s.id, data: data});
        hideSuggest();
        s.term.focus();
    }

    function handleSuggestKeys(s, data) {
        if (!autoSuggest) return false;

        if (data === '\t') {
            if (suggestVisible && suggestItems.length) {
                acceptSuggest(s, suggestItems[suggestIndex]);
                return true;
            }
            refreshSuggest(s, true);
            if (suggestVisible && suggestItems.length) {
                acceptSuggest(s, suggestItems[suggestIndex]);
                return true;
            }
            return false;  // 没有候选时交给远程 shell 自己补全
        }
        if (suggestVisible && (data === '\x1b[A' || data === '\x1b[B')) {
            var delta = data === '\x1b[A' ? -1 : 1;
            suggestIndex = (suggestIndex + delta + suggestItems.length) % suggestItems.length;
            renderSuggest(s);
            return true;
        }
        if (suggestVisible && data === '\x1b') {
            hideSuggest();
            return true;
        }
        return false;
    }

    /* --------------------------- 工具 --------------------------- */

    function setStatus(state, text) {
        statusEl.setAttribute('data-state', state);
        statusText.textContent = text;
    }

    function hostById(id) {
        for (var i = 0; i < hosts.length; i++) {
            if (hosts[i].id === parseInt(id, 10)) return hosts[i];
        }
        return null;
    }

    function emit(event, data) {
        data.ws = workspace;
        ensureSocket().emit(event, data);
    }

    function emitAttach(s) {
        emit('ssh_attach', {session_id: s.id, host_id: s.hostId, cols: s.term.cols, rows: s.term.rows});
    }

    function ensureSocket() {
        if (socket) return socket;
        socket = io({transports: ['websocket', 'polling']});
        socket.on('connect', function () {
            // 回到终端页时重新接入仍在运行的会话
            order.forEach(function (id) {
                var s = sessions[id];
                if (s && !s.connected) emitAttach(s);
            });
            if (activeId && sessions[activeId] && sessions[activeId].connected) {
                setStatus('online', '已连接 ' + sessions[activeId].host.endpoint);
            }
        });
        socket.on('disconnect', function () {
            Object.keys(sessions).forEach(function (id) { markState(id, 'offline'); });
            setStatus('idle', '通道断开');
        });
        socket.on('ssh_gone', function (data) {
            var s = sessions[data.session_id];
            if (!s || s.connected) return;
            // 恢复页面时会话已不在（之前被断开/回收）：保持离线，等用户点「重连」，不自动建连
            if (s.autoStart === false) {
                markState(s.id, 'offline');
                s.term.writeln('\r\n\x1b[38;5;244m连接已断开，点击工具栏「重连」可重新连接。\x1b[0m');
                if (s.id === activeId) setStatus('idle', '未连接');
                return;
            }
            startSession(s);
        });
        socket.on('ssh_ready', function (data) {
            var s = sessions[data.session_id];
            if (!s) return;
            s.connected = true;
            markState(s.id, 'online');
            s.term.writeln('');
            resizeSession(s);
            if (s.id === activeId) setStatus('online', '已连接 ' + s.host.endpoint);
        });
        socket.on('ssh_output', function (data) {
            var s = sessions[data.session_id];
            if (s) s.term.write(data.data || '');
        });
        socket.on('ssh_error', function (data) {
            var s = sessions[data.session_id];
            if (s) {
                s.connected = false;
                markState(s.id, 'error');
                s.term.writeln('\r\n\x1b[31m[错误] ' + (data.message || '连接失败') + '\x1b[0m');
                if (s.id === activeId) setStatus('error', '连接失败');
            } else {
                toast(data.message || '连接失败', 'error');
            }
        });
        socket.on('ssh_closed', function (data) {
            var s = sessions[data.session_id];
            if (!s) return;
            s.connected = false;
            markState(s.id, 'offline');
            s.term.writeln('\r\n\x1b[38;5;244m' + (data.message || '连接已关闭') + '\x1b[0m');
            if (s.id === activeId) setStatus('idle', data.message || '连接已关闭');
        });
        socket.on('exec_result', function (data) {
            showExecResult(data);
        });
        return socket;
    }

    /* --------------------------- 会话管理 --------------------------- */

    function createSession(hostId, autoConnect, forcedId) {
        var host = hostById(hostId);
        if (!host) {
            toast('请先到「主机管理」添加主机', 'error');
            return null;
        }
        var id = forcedId || ('s' + (++seq));
        if (!forcedId) seq = Math.max(seq, order.length + 1);
        var pane = document.createElement('div');
        pane.className = 'term-pane';
        pane.style.display = 'none';
        termStack.appendChild(pane);

        var term = new Terminal({
            cursorBlink: true,
            fontSize: 14,
            fontFamily: 'Menlo, Monaco, Consolas, "Courier New", monospace',
            scrollback: 5000,
            convertEol: false,
            theme: {
                background: '#0b1220', foreground: '#d7e0f0', cursor: '#4f8cff',
                black: '#0b1220', red: '#ff6b6b', green: '#4ade80', yellow: '#fbbf24',
                blue: '#60a5fa', magenta: '#c084fc', cyan: '#22d3ee', white: '#e2e8f0'
            }
        });
        var fit = new FitAddon.FitAddon();
        term.loadAddon(fit);
        term.open(pane);

        var s = {
            id: id, host: host, hostId: host.id, term: term, fit: fit, pane: pane,
            connected: false, buffer: '', state: 'busy'
        };

        term.onData(function (data) {
            if (!s.connected) {
                if (data === '\r') term.writeln('\r\n\x1b[33m尚未连接，请点击「重连」。\x1b[0m');
                return;
            }
            if (handleSuggestKeys(s, data)) return;

            emit('ssh_input', {session_id: id, data: data});
            if (data === '\r' || data === '\n') {
                var cmd = s.buffer.trim();
                if (cmd) {
                    emit('ssh_command_log', {session_id: id, host_id: s.hostId, command: cmd});
                    pushHistory(cmd);
                }
                s.buffer = '';
                hideSuggest();
            } else if (data === '\u007f') {
                s.buffer = s.buffer.slice(0, -1);
                refreshSuggest(s);
            } else if (data === '\u0003') {
                s.buffer = '';
                hideSuggest();
            } else if (data.length === 1 && data >= ' ') {
                s.buffer += data;
                refreshSuggest(s);
            } else {
                hideSuggest();
            }
        });
        term.onResize(function () {
            resizeSession(s);
        });

        sessions[id] = s;
        order.push(id);
        createTab(s);
        activate(id);
        persistTabs();
        if (autoConnect !== false) startSession(s);
        return s;
    }

    function createTab(s) {
        var tab = document.createElement('div');
        tab.className = 'tab';
        tab.setAttribute('data-id', s.id);
        tab.innerHTML =
            '<i class="tab-dot" data-state="busy"></i>' +
            '<span class="tab-title"></span>' +
            '<span class="tab-close" title="关闭">×</span>';
        tab.querySelector('.tab-title').textContent = s.host.name;
        tab.title = s.host.name + ' — ' + s.host.endpoint;
        tab.addEventListener('click', function (event) {
            if (event.target.classList.contains('tab-close')) return;
            activate(s.id);
        });
        tab.addEventListener('dblclick', function () { connect(s); });
        tab.querySelector('.tab-close').addEventListener('click', function (event) {
            event.stopPropagation();
            closeSession(s.id);
        });
        s.tab = tab;
        s.dot = tab.querySelector('.tab-dot');
        tabList.appendChild(tab);
    }

    function markState(id, state) {
        var s = sessions[id];
        if (!s) return;
        s.state = state;
        if (s.dot) s.dot.setAttribute('data-state', state);
    }

    function activate(id) {
        var s = sessions[id];
        if (!s) return;
        activeId = id;
        hideSuggest();
        Object.keys(sessions).forEach(function (key) {
            sessions[key].pane.style.display = key === id ? 'block' : 'none';
            if (sessions[key].tab) sessions[key].tab.classList.toggle('active', key === id);
        });
        termEmpty.style.display = 'none';
        fitSession(s);
        resizeSession(s);
        s.term.focus();
        var label = {online: '已连接 ', busy: '连接中 ', offline: '未连接 ', error: '连接失败 '}[s.state] || '';
        setStatus(s.state, label + (s.state === 'online' ? s.host.endpoint : s.host.name));
        activateHooks.forEach(function (cb) {
            try { cb(activeInfo()); } catch (e) { /* 忽略外部回调异常 */ }
        });
    }

    function activeInfo() {
        var s = sessions[activeId];
        return s ? {id: s.id, hostId: s.hostId, name: s.host.name, endpoint: s.host.endpoint,
                    connected: s.connected} : null;
    }

    function fitSession(s) {
        try {
            s.fit.fit();
        } catch (e) {
            /* 容器隐藏时忽略 */
        }
        trimOverflow(s);
    }

    // 单元格高度是小数时 fit 会算多一行，导致最后一行被容器裁掉（"吃字"），这里按实际渲染高度回退
    function trimOverflow(s) {
        try {
            var screen = s.term.element.querySelector('.xterm-screen');
            var viewport = s.term.element.querySelector('.xterm-viewport');
            if (!screen || !viewport) return;
            var delta = screen.offsetHeight - viewport.clientHeight;
            if (delta <= 0) return;
            var cellHeight = screen.offsetHeight / Math.max(1, s.term.rows);
            var cut = Math.ceil(delta / Math.max(1, cellHeight));
            if (cut > 0 && s.term.rows - cut >= 5) {
                s.term.resize(s.term.cols, s.term.rows - cut);
            }
        } catch (e) { /* 忽略 */ }
    }

    function refitActive() {
        var s = sessions[activeId];
        if (!s) return;
        fitSession(s);
        resizeSession(s);
    }

    function resizeSession(s) {
        if (s && s.connected && socket) {
            emit('ssh_resize', {session_id: s.id, cols: s.term.cols, rows: s.term.rows});
        }
    }

    function startSession(s) {
        s.connected = false;
        s.buffer = '';
        markState(s.id, 'busy');
        setStatus('busy', '正在连接 ' + s.host.endpoint + ' ...');
        s.term.writeln('\x1b[38;5;244m正在连接 ' + s.host.endpoint + ' ...\x1b[0m');
        emit('ssh_start', {session_id: s.id, host_id: s.hostId, cols: s.term.cols, rows: s.term.rows});
    }

    function connect(s) {
        // 重连：优先恢复服务端仍在运行的会话，保留原有输入与上下文（不新建 SSH 连接）
        s = s || sessions[activeId];
        if (!s) return;
        s.connected = false;
        s.buffer = '';
        markState(s.id, 'busy');
        setStatus('busy', '正在恢复 ' + s.host.endpoint + ' ...');
        emitAttach(s);
    }

    function disconnect(s) {
        s = s || sessions[activeId];
        if (!s) return;
        emit('ssh_stop', {session_id: s.id});
        s.connected = false;
        markState(s.id, 'offline');
        setStatus('idle', '已断开 ' + s.host.name);
    }

    function closeSession(id) {
        var s = sessions[id];
        if (!s) return;
        if (suggestSession && suggestSession.id === id) hideSuggest();
        emit('ssh_stop', {session_id: id});
        if (s.term) s.term.dispose();
        if (s.pane && s.pane.parentNode) s.pane.parentNode.removeChild(s.pane);
        if (s.tab && s.tab.parentNode) s.tab.parentNode.removeChild(s.tab);
        delete sessions[id];
        order = order.filter(function (item) { return item !== id; });
        persistTabs();
        if (activeId === id) {
            activeId = null;
            var next = order[order.length - 1];
            if (next) activate(next);
            else {
                termEmpty.style.display = 'flex';
                setStatus('idle', '未连接');
            }
        }
        closeHooks.forEach(function (cb) {
            try { cb(id, activeInfo()); } catch (e) { /* 忽略外部回调异常 */ }
        });
    }

    /* --------------------------- 快速执行 --------------------------- */

    function showExecResult(data) {
        var s = sessions[data.session_id];
        execPanel.style.display = 'flex';
        refitActive();  // 结果面板占位后终端高度变化，需重新适配，避免底部被裁
        execTitle.textContent = '快速执行结果' + (s ? '（' + s.host.name + '）' : '');
        var head = '$ ' + (data.command || '') + '\n';
        var body = (data.output || '') + (data.error ? '\n[stderr]\n' + data.error : '');
        var tail = '\n\n[退出码 ' + data.exit_code + ' · 耗时 ' + (data.duration || 0) + 's · ' + (data.host || '') + ']';
        execOutput.textContent = head + (body || '（无输出）') + tail;
        execOutput.scrollTop = 0;
    }

    function runCommand(command) {
        var s = sessions[activeId];
        var hostId = s ? s.hostId : parseInt(hostSelect.value, 10);
        var sessionId = s ? s.id : 'adhoc';
        if (!hostId) {
            toast('请先选择主机或新建连接', 'error');
            return;
        }
        emit('ssh_exec', {session_id: sessionId, host_id: hostId, command: command});
        setStatus('busy', '执行中...');
        toast('已提交执行：' + command.slice(0, 40), 'info');
    }

    document.getElementById('btnExecClose').addEventListener('click', function () {
        execPanel.style.display = 'none';
        refitActive();
    });
    document.getElementById('btnExecCopy').addEventListener('click', function () {
        copyText(execOutput.textContent);
    });

    /* --------------------------- 命令侧栏 --------------------------- */

    var cmdList = document.getElementById('cmdList');
    var cmdSearch = document.getElementById('cmdSearch');

    function escapeHtml(text) {
        var div = document.createElement('div');
        div.textContent = text || '';
        return div.innerHTML;
    }

    function renderCommands(keyword) {
        keyword = (keyword || '').trim().toLowerCase();
        var list = snippets.filter(function (item) {
            if (!keyword) return true;
            return (item.name + ' ' + item.command + ' ' + item.category).toLowerCase().indexOf(keyword) >= 0;
        });

        cmdList.innerHTML = '';
        if (!list.length) {
            cmdList.innerHTML = '<div class="side-empty">没有匹配的命令</div>';
            return;
        }

        var currentCat = null;
        var group = null;
        list.forEach(function (item) {
            if (item.category !== currentCat) {
                currentCat = item.category;
                group = document.createElement('div');
                group.className = 'side-group';
                var title = document.createElement('div');
                title.className = 'side-group-title';
                title.textContent = currentCat;
                group.appendChild(title);
                cmdList.appendChild(group);
            }
            var row = document.createElement('div');
            row.className = 'side-item';
            row.innerHTML =
                '<div class="side-item-name">' + escapeHtml(item.name) +
                (item.dangerous ? ' <span class="badge badge-danger">高危</span>' : '') +
                '</div><div class="side-item-cmd">' + escapeHtml(item.command) + '</div>';

            var ops = document.createElement('div');
            ops.className = 'side-item-ops';

            var insertBtn = document.createElement('button');
            insertBtn.className = 'btn btn-sm btn-ghost';
            insertBtn.textContent = '插入';
            insertBtn.addEventListener('click', function () {
                var s = sessions[activeId];
                if (!s || !s.connected) {
                    toast('当前标签未连接，已改为直接执行', 'info');
                    runCommand(item.command);
                    return;
                }
                emit('ssh_input', {session_id: s.id, data: item.command});
                s.term.focus();
            });

            var runBtn = document.createElement('button');
            runBtn.className = 'btn btn-sm btn-primary';
            runBtn.textContent = '执行';
            runBtn.addEventListener('click', function () {
                runCommand(item.command);
            });

            ops.appendChild(insertBtn);
            ops.appendChild(runBtn);
            row.appendChild(ops);
            group.appendChild(row);
        });
    }

    cmdSearch.addEventListener('input', function () {
        renderCommands(cmdSearch.value);
    });
    renderCommands('');

    /* --------------------------- 工具栏 --------------------------- */

    function newConnection() {
        var hostId = parseInt(hostSelect.value, 10);
        if (!hostId) {
            toast('请先到「主机管理」添加主机', 'error');
            return;
        }
        createSession(hostId, true);
    }

    document.getElementById('btnNew').addEventListener('click', newConnection);
    document.getElementById('btnNewTab').addEventListener('click', newConnection);
    document.getElementById('btnReconnect').addEventListener('click', function () {
        if (!sessions[activeId]) { newConnection(); return; }
        connect(sessions[activeId]);
    });
    document.getElementById('btnDisconnect').addEventListener('click', function () { disconnect(); });
    document.getElementById('btnClear').addEventListener('click', function () {
        if (sessions[activeId]) sessions[activeId].term.clear();
    });

    window.addEventListener('resize', refitActive);

    // 容器尺寸变化（侧栏/结果面板/布局变化）时自动重新适配
    if (window.ResizeObserver) {
        new ResizeObserver(function () {
            refitActive();
        }).observe(termStack);
    }

    document.getElementById('termStack').addEventListener('paste', function (event) {
        var s = sessions[activeId];
        if (!s || !s.connected) return;
        var text = (event.clipboardData || window.clipboardData).getData('text');
        if (text) emit('ssh_input', {session_id: s.id, data: text});
    });

    // 快捷键：Ctrl+Alt+T 新建连接，Ctrl+Alt+W 关闭当前标签
    document.addEventListener('keydown', function (event) {
        if (event.ctrlKey && event.altKey && event.key.toLowerCase() === 'b') {
            event.preventDefault();
            if (btnToggleSide) btnToggleSide.click();
            return;
        }
        if (event.ctrlKey && event.altKey && event.key.toLowerCase() === 't') {
            event.preventDefault();
            newConnection();
        }
        if (event.ctrlKey && event.altKey && event.key.toLowerCase() === 'w') {
            event.preventDefault();
            if (activeId) closeSession(activeId);
        }
    });

    /* --------------------------- 初始化 --------------------------- */

    function restoreTabs() {
        var saved = [];
        try {
            saved = JSON.parse(sessionStorage.getItem('sshTabs') || '[]');
        } catch (e) {
            saved = [];
        }
        var count = 0;
        saved.forEach(function (item) {
            if (!item || !item.id || sessions[item.id]) return;
            if (!hostById(item.hostId)) return;  // 主机已被删除则跳过
            var s = createSession(item.hostId, false, item.id);
            if (!s) return;
            count++;
            s.autoStart = false;  // 恢复的标签不自动建连
            if (socket && socket.connected) emitAttach(s);
        });
        return count;
    }

    /* 右侧命令库：默认隐藏，可切换，状态记住在 localStorage */
    var termSide = document.getElementById('termSide');
    var btnToggleSide = document.getElementById('btnToggleSide');

    function setSideVisible(visible) {
        if (!termSide) return;
        termSide.classList.toggle('is-hidden', !visible);
        if (btnToggleSide) btnToggleSide.textContent = visible ? '隐藏命令库' : '命令库';
        try {
            localStorage.setItem('sshSideHidden', visible ? '0' : '1');
        } catch (e) { /* 忽略存储失败 */ }
        refitActive();
    }

    if (termSide) {
        // 默认隐藏，显式保存过 '0' 才展开
        setSideVisible(localStorage.getItem('sshSideHidden') === '0');
    }
    if (btnToggleSide) {
        btnToggleSide.addEventListener('click', function () {
            setSideVisible(!termSide || termSide.classList.contains('is-hidden'));
        });
    }

    var chkSuggest = document.getElementById('chkSuggest');
    if (chkSuggest) {
        chkSuggest.checked = autoSuggest;
        chkSuggest.addEventListener('change', function () {
            autoSuggest = chkSuggest.checked;
            localStorage.setItem('sshAutoSuggest', autoSuggest ? '1' : '0');
            if (!autoSuggest) hideSuggest();
        });
    }
    loadHistory();

    restoreTabs();
    // 仅当从「主机管理 → 连接」等显式入口进入（?host=）时才自动建连
    if (window.APP_AUTO_CONNECT && initHostId) {
        createSession(initHostId, true);
    }

    /* 供文件面板等外部模块使用的最小接口 */
    window.SSHTerm = {
        getActive: activeInfo,
        getHosts: function () { return hosts; },
        onActivate: function (cb) { activateHooks.push(cb); if (activeId) cb(activeInfo()); },
        onClose: function (cb) { closeHooks.push(cb); }
    };

    // 页面离开：通知服务端进入宽限期，切页/刷新回来会自动重新接入，关闭浏览器则回收
    window.addEventListener('beforeunload', function () {
        if (socket && socket.connected && order.length) {
            socket.emit('ssh_pending_close', {ws: workspace});
        }
    });
})();
