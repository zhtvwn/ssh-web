/* 通用交互组件：toast、模态框、复制、确认 */

function toast(message, type) {
    type = type || 'info';
    var box = document.querySelector('.toast-wrap');
    if (!box) {
        box = document.createElement('div');
        box.className = 'toast-wrap';
        document.body.appendChild(box);
    }
    var el = document.createElement('div');
    el.className = 'toast toast-' + type;
    el.textContent = message;
    box.appendChild(el);
    setTimeout(function () {
        el.classList.add('hide');
        setTimeout(function () { el.remove(); }, 300);
    }, 2600);
}

function openModal(id) {
    var modal = document.getElementById(id);
    if (!modal) return;
    modal.classList.add('open');
}

function closeModal(modal) {
    if (typeof modal === 'string') modal = document.getElementById(modal);
    if (modal) modal.classList.remove('open');
}

/* 自定义确认弹窗：表单加 data-confirm="提示语" 即可自动接管提交 */
var confirmCallback = null;

function showConfirm(message, onConfirm, okText) {
    document.getElementById('confirmText').textContent = message;
    var okBtn = document.getElementById('btnConfirmOk');
    okBtn.textContent = okText || '确认删除';
    confirmCallback = onConfirm;
    openModal('modal-confirm');
    okBtn.focus();
}

document.getElementById('btnConfirmOk').addEventListener('click', function () {
    var callback = confirmCallback;
    confirmCallback = null;
    closeModal('modal-confirm');
    if (callback) callback();
});

document.addEventListener('submit', function (event) {
    var form = event.target;
    if (!form || !form.getAttribute || !form.getAttribute('data-confirm')) return;
    if (form.getAttribute('data-confirmed') === '1') return;  // 已确认，正常提交
    event.preventDefault();
    var target = form;
    showConfirm(form.getAttribute('data-confirm'), function () {
        target.setAttribute('data-confirmed', '1');
        target.submit();
    });
});

function copyText(text) {
    if (navigator.clipboard && window.isSecureContext) {
        navigator.clipboard.writeText(text).then(function () {
            toast('已复制到剪贴板', 'success');
        }, function () {
            fallbackCopy(text);
        });
    } else {
        fallbackCopy(text);
    }
}

function fallbackCopy(text) {
    var ta = document.createElement('textarea');
    ta.value = text;
    ta.style.position = 'fixed';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    try {
        document.execCommand('copy');
        toast('已复制到剪贴板', 'success');
    } catch (e) {
        toast('复制失败，请手动选择复制', 'error');
    }
    ta.remove();
}

document.addEventListener('click', function (event) {
    var opener = event.target.closest('[data-modal-open]');
    if (opener) {
        event.preventDefault();
        openModal(opener.getAttribute('data-modal-open'));
        return;
    }
    var closer = event.target.closest('[data-modal-close]');
    if (closer) {
        event.preventDefault();
        closeModal(closer.closest('.modal'));
    }
});

document.addEventListener('keydown', function (event) {
    if (event.key === 'Escape') {
        document.querySelectorAll('.modal.open').forEach(function (m) { m.classList.remove('open'); });
    }
});

function bindCopyButtons() {
    document.querySelectorAll('[data-copy]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            copyText(btn.getAttribute('data-copy'));
        });
    });
}

function syncAuthFields(select) {
    var group = select.getAttribute('data-target');
    var isKey = select.value === 'key';
    document.querySelectorAll('[data-group="' + group + '"]').forEach(function (el) {
        el.style.display = 'none';
    });
    var target = document.querySelector(
        (isKey ? '.auth-field-key' : '.auth-field-password') + '[data-group="' + group + '"]'
    );
    if (target) target.style.display = isKey ? 'block' : 'flex';
}

function bindHostModals() {
    document.querySelectorAll('.auth-select').forEach(function (select) {
        syncAuthFields(select);
        select.addEventListener('change', function () { syncAuthFields(select); });
    });

    var editForm = document.getElementById('form-host-edit');
    document.querySelectorAll('[data-modal-open="modal-host-edit"]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            if (!editForm) return;
            var id = btn.getAttribute('data-id');
            editForm.setAttribute('action', window.HOST_UPDATE_URL.replace('__ID__', id));
            editForm.querySelector('[name=name]').value = btn.getAttribute('data-name');
            editForm.querySelector('[name=hostname]').value = btn.getAttribute('data-hostname');
            editForm.querySelector('[name=port]').value = btn.getAttribute('data-port');
            editForm.querySelector('[name=username]').value = btn.getAttribute('data-username');
            editForm.querySelector('[name=remark]').value = btn.getAttribute('data-remark');
            var authSelect = editForm.querySelector('[name=auth_type]');
            authSelect.value = btn.getAttribute('data-auth_type');
            syncAuthFields(authSelect);
            editForm.querySelector('[name=verify_host_key]').checked = btn.getAttribute('data-verify_host_key') === '1';
            var sharedWrap = document.getElementById('wrap-shared');
            var sharedInput = editForm.querySelector('[name=shared]');
            if (btn.getAttribute('data-can_share') !== '1') {
                sharedWrap.style.display = 'none';
                sharedInput.checked = false;
            } else {
                sharedWrap.style.display = 'flex';
                sharedInput.checked = btn.getAttribute('data-shared') === '1';
            }
        });
    });
}

function bindUserModals() {
    var editForm = document.getElementById('form-user-edit');
    document.querySelectorAll('[data-modal-open="modal-user-edit"]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            if (!editForm) return;
            editForm.setAttribute('action', window.USER_UPDATE_URL.replace('__ID__', btn.getAttribute('data-id')));
            editForm.querySelector('[name=username]').value = btn.getAttribute('data-username');
            editForm.querySelector('[name=role]').value = btn.getAttribute('data-role');
            editForm.querySelector('[name=remark]').value = btn.getAttribute('data-remark');
            editForm.querySelector('[name=active]').checked = btn.getAttribute('data-active') === '1';
        });
    });

    var resetForm = document.getElementById('form-user-reset');
    document.querySelectorAll('[data-modal-open="modal-user-reset"]').forEach(function (btn) {
        btn.addEventListener('click', function () {
            if (!resetForm) return;
            resetForm.setAttribute('action', window.USER_UPDATE_URL.replace('__ID__', btn.getAttribute('data-id')));
            document.getElementById('reset-username').textContent = btn.getAttribute('data-username');
        });
    });
}
