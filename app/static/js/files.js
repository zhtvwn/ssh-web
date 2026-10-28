/* 远端文件面板：目录浏览、拖拽上传、下载与文本预览
 * 位于 Web 终端页底部，跟随当前激活的终端标签对应的主机。
 */

(function () {
    var panel = document.getElementById('filePanel');
    if (!panel || !window.SSHTerm) return;

    var els = {
        body: document.getElementById('fileBody'),
        tree: document.getElementById('fileTree'),
        rows: document.getElementById('fileRows'),
        path: document.getElementById('filePath'),
        host: document.getElementById('fileHost'),
        foot: document.getElementById('fileFoot'),
        drop: document.getElementById('fileDrop'),
        input: document.getElementById('fileInput'),
        toggle: document.getElementById('btnToggleFiles')
    };

    var state = {hostId: null, path: '', home: '', parent: null};
    var visible = localStorage.getItem('sshFilesVisible') === '1';
    var treeIndex = {};
    var inputCallback = null;

    /* --------------------------- 基础工具 --------------------------- */

    function setFoot(text) {
        if (els.foot) els.foot.textContent = text;
    }

    function formatSize(size) {
        size = size || 0;
        if (size < 1024) return size + ' B';
        var units = ['KB', 'MB', 'GB', 'TB'];
        var value = size / 1024;
        var index = 0;
        while (value >= 1024 && index < units.length - 1) {
            value /= 1024;
            index++;
        }
        return (value >= 100 ? value.toFixed(0) : value.toFixed(1)) + ' ' + units[index];
    }

    function currentHost() {
        var active = window.SSHTerm.getActive();
        if (active) {
            return {hostId: active.hostId, name: active.name, endpoint: active.endpoint};
        }
        var select = document.getElementById('hostSelect');
        var id = parseInt(select ? select.value : 0, 10);
        var list = window.SSHTerm.getHosts() || [];
        for (var i = 0; i < list.length; i++) {
            if (list[i].id === id) return {hostId: id, name: list[i].name, endpoint: list[i].endpoint};
        }
        return null;
    }

    function requireHost() {
        var host = currentHost();
        if (!host) toast('请先在工具栏选择主机或新建一个连接', 'error');
        return host;
    }

    function postJson(url, payload, onOk) {
        fetch(url, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(payload || {})
        }).then(function (r) {
            return r.json();
        }).then(function (data) {
            if (!data.ok) {
                toast(data.message || '操作失败', 'error');
                setFoot(data.message || '操作失败');
                return;
            }
            toast(data.message || '操作成功', 'success');
            if (onOk) onOk(data);
        }).catch(function (err) {
            toast('操作失败：' + err, 'error');
        });
    }

    function makeButton(text, className, handler) {
        var btn = document.createElement('button');
        btn.className = 'btn btn-sm ' + className;
        btn.textContent = text;
        btn.addEventListener('click', function (event) {
            event.stopPropagation();
            handler();
        });
        return btn;
    }

    /* --------------------------- 目录列表 --------------------------- */

    function load(path) {
        var host = requireHost();
        if (!host) return;
        var target = path || (state.hostId === host.hostId && state.path ? state.path : '');
        setFoot('正在读取 ' + (target || '远端家目录') + ' ...');
        fetch('/api/files/list?host_id=' + host.hostId + '&path=' + encodeURIComponent(target))
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!data.ok) {
                    setFoot(data.message || '读取目录失败');
                    toast(data.message || '读取目录失败', 'error');
                    return;
                }
                state.hostId = host.hostId;
                state.path = data.path;
                state.home = data.home;
                state.parent = data.parent;
                els.path.value = data.path;
                els.host.textContent = host.name + ' · ' + data.path;
                renderRows(data.entries || []);
                renderTree(data.path);
                setFoot('共 ' + (data.entries || []).length + ' 项 · 拖拽本地文件到此区域即可上传到 ' + data.path);
            })
            .catch(function (err) {
                setFoot('读取失败：' + err);
            });
    }

    function renderRows(entries) {
        els.rows.innerHTML = '';
        if (!entries.length) {
            var empty = document.createElement('tr');
            empty.innerHTML = '<td colspan="5" class="file-empty">（空目录，可拖拽文件到此处上传）</td>';
            els.rows.appendChild(empty);
            return;
        }
        entries.forEach(function (item) {
            var tr = document.createElement('tr');
            tr.className = 'file-row' + (item.is_dir ? ' is-dir' : '');
            tr.innerHTML =
                '<td class="file-cell-name"><span class="file-icon"></span><span class="file-text"></span></td>' +
                '<td class="file-cell-size"></td>' +
                '<td class="file-cell-time"></td>' +
                '<td class="file-cell-mode"></td>' +
                '<td class="file-cell-ops"></td>';
            tr.querySelector('.file-icon').textContent = item.is_dir ? '📁' : (item.is_link ? '🔗' : '📄');
            tr.querySelector('.file-text').textContent = item.name;
            tr.querySelector('.file-cell-size').textContent = item.is_dir ? '—' : formatSize(item.size);
            tr.querySelector('.file-cell-time').textContent = item.mtime || '';
            tr.querySelector('.file-cell-mode').textContent = item.mode || '';

            var ops = tr.querySelector('.file-cell-ops');
            if (item.is_dir) {
                ops.appendChild(makeButton('打开', 'btn-primary', function () { load(item.path); }));
            } else {
                ops.appendChild(makeButton('下载', 'btn-primary', function () { download(item); }));
                ops.appendChild(makeButton('预览', 'btn-ghost', function () { preview(item); }));
            }
            ops.appendChild(makeButton('重命名', 'btn-ghost', function () { rename(item); }));
            ops.appendChild(makeButton('删除', 'btn-danger', function () { remove(item); }));

            tr.addEventListener('dblclick', function () {
                if (item.is_dir) load(item.path);
                else preview(item);
            });
            els.rows.appendChild(tr);
        });
    }

    /* --------------------------- 目录树 --------------------------- */

    function makeNode(path, name) {
        var el = document.createElement('div');
        el.className = 'ft-node';
        var row = document.createElement('div');
        row.className = 'ft-row';
        row.innerHTML = '<span class="ft-arrow">▸</span><span class="ft-icon">📁</span><span class="ft-name"></span>';
        row.querySelector('.ft-name').textContent = name;
        row.title = path;
        var children = document.createElement('div');
        children.className = 'ft-children';
        children.style.display = 'none';
        el.appendChild(row);
        el.appendChild(children);

        var node = {
            el: el, row: row, children: children, path: path, loaded: false,
            arrow: row.querySelector('.ft-arrow')
        };
        row.addEventListener('click', function () {
            load(path);
            markActive(path);
            if (children.style.display === 'block') {
                collapseNode(node);
            } else {
                expandNode(node);
            }
        });
        return node;
    }

    function openNode(node) {
        node.children.style.display = 'block';
        node.arrow.textContent = '▾';
    }

    function collapseNode(node) {
        node.children.style.display = 'none';
        node.arrow.textContent = '▸';
    }

    function expandNode(node, done) {
        if (node.loaded) {
            openNode(node);
            if (done) done();
            return;
        }
        var host = currentHost();
        if (!host) {
            if (done) done();
            return;
        }
        fetch('/api/files/list?host_id=' + host.hostId + '&path=' + encodeURIComponent(node.path))
            .then(function (r) { return r.json(); })
            .then(function (data) {
                node.children.innerHTML = '';
                var dirs = ((data && data.entries) || []).filter(function (e) { return e.is_dir; });
                if (!dirs.length) {
                    node.children.innerHTML = '<div class="ft-empty">（无子目录）</div>';
                }
                dirs.forEach(function (dir) {
                    var child = makeNode(dir.path, dir.name);
                    node.children.appendChild(child.el);
                    treeIndex[dir.path] = child;
                });
                node.loaded = true;
                openNode(node);
                if (done) done();
            })
            .catch(function () {
                node.loaded = true;
                openNode(node);
                if (done) done();
            });
    }

    function markActive(path) {
        Object.keys(treeIndex).forEach(function (key) {
            treeIndex[key].row.classList.toggle('active', key === path);
        });
    }

    function expandTo(path) {
        var parts = (path || '').split('/').filter(Boolean);
        var chain = ['/'];
        var cur = '';
        parts.forEach(function (part) {
            cur += '/' + part;
            chain.push(cur);
        });
        var index = 0;
        (function step() {
            if (index >= chain.length) {
                markActive(path);
                return;
            }
            var node = treeIndex[chain[index++]];
            if (!node) {
                step();
                return;
            }
            expandNode(node, step);
        })();
    }

    function renderTree(currentPath) {
        if (!els.tree) return;
        els.tree.innerHTML = '';
        treeIndex = {};
        if (!currentPath || currentPath.charAt(0) !== '/') {
            els.tree.innerHTML = '<div class="ft-empty">当前路径不支持目录树</div>';
            return;
        }
        var root = makeNode('/', '/');
        els.tree.appendChild(root.el);
        treeIndex['/'] = root;
        expandTo(currentPath);
    }

    /* --------------------------- 下载 / 预览 / 编辑 --------------------------- */

    function download(item) {
        var host = requireHost();
        if (!host) return;
        var url = '/api/files/download?host_id=' + host.hostId + '&path=' + encodeURIComponent(item.path);
        setFoot('正在下载 ' + item.name + ' ...');
        fetch(url).then(function (r) {
            var type = r.headers.get('content-type') || '';
            if (type.indexOf('application/json') >= 0) {
                return r.json().then(function (data) {
                    throw new Error(data.message || '下载失败');
                });
            }
            return r.blob();
        }).then(function (blob) {
            var link = document.createElement('a');
            link.href = URL.createObjectURL(blob);
            link.download = item.name;
            document.body.appendChild(link);
            link.click();
            link.remove();
            setTimeout(function () { URL.revokeObjectURL(link.href); }, 5000);
            setFoot('已下载 ' + item.name);
            toast('已下载 ' + item.name, 'success');
        }).catch(function (err) {
            setFoot('下载失败：' + err.message);
            toast('下载失败：' + err.message, 'error');
        });
    }

    function preview(item) {
        var host = requireHost();
        if (!host) return;
        fetch('/api/files/read?host_id=' + host.hostId + '&path=' + encodeURIComponent(item.path))
            .then(function (r) { return r.json(); })
            .then(function (data) {
                if (!data.ok) {
                    toast(data.message || '预览失败', 'error');
                    return;
                }
                document.getElementById('fileViewName').textContent =
                    data.name + (data.truncated ? '（仅显示前 200KB）' : '');
                document.getElementById('fileViewContent').textContent = data.content || '（空文件）';
                openModal('modal-file-view');
            })
            .catch(function (err) { toast('预览失败：' + err, 'error'); });
    }

    function rename(item) {
        if (!requireHost()) return;
        askInput('重命名', '新名称', item.name, function (name) {
            postJson('/api/files/rename', {host_id: currentHost().hostId, path: item.path, name: name},
                function () { load(state.path); });
        });
    }

    function remove(item) {
        if (!requireHost()) return;
        showConfirm('确定删除远端「' + item.path + '」？' + (item.is_dir ? '（目录需为空）' : ''), function () {
            postJson('/api/files/remove', {host_id: currentHost().hostId, path: item.path},
                function () { load(state.path); });
        }, '确认删除');
    }

    function askInput(title, label, value, callback) {
        document.getElementById('fileInputTitle').textContent = title;
        document.getElementById('fileInputLabel').textContent = label;
        var input = document.getElementById('fileInputValue');
        input.value = value || '';
        inputCallback = callback;
        openModal('modal-file-input');
        setTimeout(function () { input.focus(); input.select(); }, 50);
    }

    var btnFileInputOk = document.getElementById('btnFileInputOk');
    if (btnFileInputOk) {
        btnFileInputOk.addEventListener('click', function () {
            var value = document.getElementById('fileInputValue').value.trim();
            var callback = inputCallback;
            inputCallback = null;
            closeModal('modal-file-input');
            if (callback && value) callback(value);
        });
        document.getElementById('fileInputValue').addEventListener('keydown', function (event) {
            if (event.key === 'Enter') {
                event.preventDefault();
                btnFileInputOk.click();
            }
        });
    }

    /* --------------------------- 上传（选择 / 拖拽） --------------------------- */

    function uploadFiles(list) {
        var host = requireHost();
        if (!host || !list || !list.length) return;
        var form = new FormData();
        form.append('host_id', host.hostId);
        form.append('path', state.path || '');
        Array.prototype.forEach.call(list, function (file) { form.append('file', file); });

        setFoot('正在上传 ' + list.length + ' 个文件到 ' + (state.path || '家目录') + ' ...');
        var xhr = new XMLHttpRequest();
        xhr.open('POST', '/api/files/upload');
        xhr.upload.onprogress = function (event) {
            if (event.lengthComputable) {
                setFoot('上传中 ' + Math.round(event.loaded / event.total * 100) + '%');
            }
        };
        xhr.onload = function () {
            var data = null;
            try {
                data = JSON.parse(xhr.responseText);
            } catch (e) {
                data = null;
            }
            if (!data) {
                var msg = xhr.status === 413 ? '上传内容超过大小限制' : '上传失败（HTTP ' + xhr.status + '）';
                setFoot(msg);
                toast(msg, 'error');
                return;
            }
            setFoot(data.message || '');
            toast(data.message || '上传完成', data.ok ? 'success' : 'error');
            load(state.path);
        };
        xhr.onerror = function () {
            setFoot('上传失败：网络错误');
            toast('上传失败', 'error');
        };
        xhr.send(form);
    }

    function showDrop(show) {
        if (els.drop) els.drop.classList.toggle('show', !!show);
    }

    if (els.body) {
        els.body.addEventListener('dragenter', function (event) {
            event.preventDefault();
            showDrop(true);
        });
        els.body.addEventListener('dragover', function (event) {
            event.preventDefault();
            if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
            showDrop(true);
        });
        els.body.addEventListener('dragleave', function (event) {
            if (event.relatedTarget && els.body.contains(event.relatedTarget)) return;
            showDrop(false);
        });
        els.body.addEventListener('drop', function (event) {
            event.preventDefault();
            showDrop(false);
            uploadFiles(event.dataTransfer ? event.dataTransfer.files : null);
        });
    }

    /* --------------------------- 面板开关与工具栏 --------------------------- */

    function setVisible(value) {
        visible = !!value;
        panel.style.display = visible ? 'flex' : 'none';
        if (els.toggle) els.toggle.textContent = visible ? '隐藏文件' : '文件';
        try {
            localStorage.setItem('sshFilesVisible', visible ? '1' : '0');
        } catch (e) { /* 忽略存储失败 */ }
        if (visible) load(state.path);
    }

    if (els.toggle) {
        els.toggle.addEventListener('click', function () { setVisible(!visible); });
    }
    var btnClose = document.getElementById('fileClose');
    if (btnClose) btnClose.addEventListener('click', function () { setVisible(false); });

    var btnUpload = document.getElementById('fileUploadBtn');
    if (btnUpload) btnUpload.addEventListener('click', function () {
        if (!requireHost()) return;
        els.input.click();
    });
    if (els.input) {
        els.input.addEventListener('change', function () {
            uploadFiles(this.files);
            this.value = '';
        });
    }

    var btnMkdir = document.getElementById('fileMkdir');
    if (btnMkdir) btnMkdir.addEventListener('click', function () {
        var host = requireHost();
        if (!host) return;
        askInput('新建目录', '目录名（创建于 ' + (state.path || '家目录') + '）', '', function (name) {
            postJson('/api/files/mkdir', {host_id: host.hostId, path: state.path, name: name},
                function () { load(state.path); });
        });
    });

    var btnRefresh = document.getElementById('fileRefresh');
    if (btnRefresh) btnRefresh.addEventListener('click', function () { load(state.path); });
    var btnGo = document.getElementById('fileGo');
    if (btnGo) btnGo.addEventListener('click', function () { load(els.path.value.trim()); });
    if (els.path) {
        els.path.addEventListener('keydown', function (event) {
            if (event.key === 'Enter') load(els.path.value.trim());
        });
    }
    var btnUp = document.getElementById('fileUp');
    if (btnUp) btnUp.addEventListener('click', function () {
        if (state.parent) load(state.parent);
        else toast('已是根目录', 'info');
    });

    // 快捷键：Ctrl+Alt+E 打开/关闭文件面板
    document.addEventListener('keydown', function (event) {
        if (event.ctrlKey && event.altKey && event.key && event.key.toLowerCase() === 'e') {
            event.preventDefault();
            setVisible(!visible);
        }
    });

    /* --------------------------- 与终端标签联动 --------------------------- */

    window.SSHTerm.onActivate(function (info) {
        if (!visible) return;
        if (!info) {
            setFoot('暂无激活的连接，可先在工具栏新建连接或选择主机');
            return;
        }
        // 换主机则回到该主机的家目录，同一主机保持当前路径
        load(info.hostId === state.hostId ? state.path : '');
    });

    var hostSelect = document.getElementById('hostSelect');
    if (hostSelect) {
        hostSelect.addEventListener('change', function () {
            if (visible && !window.SSHTerm.getActive()) load('');
        });
    }

    setVisible(visible);
})();
