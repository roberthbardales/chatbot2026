(() => {
    'use strict';
    const panel = document.getElementById('whatsapp-panel');
    if (!panel) return;
    const el = (id) => document.getElementById(id);
    const state = { contact: null, contacts: new Map(), messages: new Map(), before: null,
        offset: null, query: '', generation: 0, listGeneration: 0, refreshing: false,
        olderBusy: false, messagesBusy: false, listBusy: false, sending: false, modeBusy: false, modeVersion: 0,
        retryBusy: false, failuresBusy: false, failureOffset: null, failures: new Map(), operationsAt: 0 };
    const endpoint = (name, id) => panel.dataset[name].replace(/\/0(?=\/|$)/, `/${id}`);
    const dateText = (value) => value ? new Date(value).toLocaleString('es') : '';
    function error(message = '') {
        el('panel-error').textContent = message;
        el('panel-error').classList.toggle('hidden', !message);
    }
    async function api(url, options = {}) {
        const response = await fetch(url, {
            credentials: 'same-origin', cache: 'no-store', ...options,
            headers: { Accept: 'application/json', ...options.headers },
        });
        if (response.redirected || response.status === 403) {
            throw new Error('La sesión expiró o no tienes permisos. Vuelve a iniciar sesión.');
        }
        const type = response.headers.get('content-type') || '';
        if (!type.includes('application/json')) throw new Error('No se pudo consultar el panel. Actualiza la página.');
        const data = await response.json();
        if (!response.ok) throw new Error(data.error || 'La petición no pudo completarse.');
        return data;
    }
    function post(url, data) {
        return api(url, { method: 'POST', headers: {
            'Content-Type': 'application/json',
            'X-CSRFToken': panel.querySelector('[name=csrfmiddlewaretoken]').value,
        }, body: JSON.stringify(data) });
    }
    function node(tag, text, classes = '') {
        const item = document.createElement(tag);
        item.textContent = text;
        item.className = classes;
        return item;
    }
    const duration = (seconds) => seconds === null ? 'sin muestras' : `${Math.round(seconds)} s`;
    async function loadOperations() {
        const data = await api(panel.dataset.operationsUrl);
        el('ops-incoming').textContent = `${data.incoming.pending} (${data.incoming.due} elegibles)`;
        el('ops-outgoing').textContent = `${data.outgoing.pending} (${data.outgoing.due} elegibles)`;
        el('ops-failed').textContent = `${data.outgoing.local_failed} / ${data.outgoing.delivery_failed}`;
        el('ops-metrics').textContent = `Espera más antigua elegible: entrada ${duration(data.incoming.oldest_due_seconds)}, salida ${duration(data.outgoing.oldest_due_seconds)}. Últimas 24 h: ${data.incoming.processed_24h} entradas procesadas, ${data.incoming.failed_24h} fallidas; media hasta procesar ${duration(data.metrics_24h.average_processing_seconds)}, hasta aceptar ${duration(data.metrics_24h.average_acceptance_seconds)}.`;
        el('ops-ai').textContent = `IA (${data.ai.day_utc}, UTC): ${data.ai.requests}/${data.ai.request_limit || 'sin límite'} llamadas, ${data.ai.reserved_tokens}/${data.ai.token_limit || 'sin límite'} tokens reservados, ${data.ai.active_calls} llamadas activas. No es facturación real.`;
        el('ops-inventory').textContent = `Inventario: ${data.inventory.products.active} productos y ${data.inventory.combos.active} combos activos. Última sincronización aplicada: ${dateText(data.inventory.last_success_at) || 'sin historial'}. Vigencia: ${{ current: 'reciente', stale: 'desactualizado', unknown: 'desconocida' }[data.inventory.freshness]}. Último intento: ${data.inventory.last_attempt?.status || 'sin historial'}.`;
        el('ops-alerts').replaceChildren(...data.alerts.map((alert) => node('li', `${alert.severity === 'critical' ? 'Crítico' : 'Aviso'}: ${alert.message}`,
            `rounded p-2 ${alert.severity === 'critical' ? 'bg-red-50 text-red-800' : 'bg-yellow-50 text-yellow-800'}`)));
        el('retry-audits').replaceChildren(...data.recent_retries.map((audit) => node('li',
            `${dateText(audit.created_at)} · Usuario ${audit.actor_id} · ${audit.parts} parte(s) · ${audit.reason}`)));
        if (!data.recent_retries.length) el('retry-audits').append(node('li', 'Sin reactivaciones registradas.'));
        state.operationsAt = Date.now();
    }
    function renderFailures() {
        const list = el('failure-list');
        list.replaceChildren();
        state.failures.forEach((failure) => {
            const li = node('li', '', 'rounded-lg border border-slate-200 p-3 text-sm');
            li.append(node('p', `${failure.name || failure.phone} · Parte ${failure.part_number} · ${failure.kind === 'delivery' ? 'Fallo de entrega' : 'Fallo local'} · Intentos ${failure.attempts}`, 'font-medium'));
            li.append(node('p', failure.content_preview, 'mt-1 whitespace-pre-wrap break-words text-xs text-slate-600'));
            li.append(node('p', failure.error || 'Sin detalle de error.', 'mt-1 break-words text-xs text-red-700'));
            if (failure.manual_retries) li.append(node('p', `Reactivaciones: ${failure.manual_retries}. Última: ${dateText(failure.last_retry_at)}`, 'text-xs text-slate-500'));
            const inspect = node('button', 'Ver conversación', 'mr-2 mt-2 rounded border px-2 py-1 text-xs text-blue-700');
            inspect.type = 'button';
            inspect.addEventListener('click', () => selectContact({ id: failure.contact_id, phone: failure.phone, name: failure.name, mode: failure.mode }));
            li.append(inspect);
            if (failure.can_retry) {
                const retry = node('button', 'Reactivar grupo desde esta parte', 'mt-2 rounded border border-amber-300 px-2 py-1 text-xs text-amber-900 disabled:opacity-40');
                retry.type = 'button'; retry.disabled = state.retryBusy;
                retry.addEventListener('click', () => retryFailure(failure));
                li.append(retry);
            } else li.append(node('p', failure.retry_blocked_reason, 'mt-2 text-xs text-slate-500'));
            list.append(li);
        });
        if (!state.failures.size) list.append(node('li', 'No hay salidas fallidas para revisar.', 'text-sm text-slate-500'));
        el('more-failures').classList.toggle('hidden', state.failureOffset === null);
    }
    async function loadFailures(append = false) {
        if (state.failuresBusy) return;
        state.failuresBusy = true; el('more-failures').disabled = true;
        try {
            const url = new URL(panel.dataset.failuresUrl, location.origin);
            if (append && state.failureOffset !== null) url.searchParams.set('offset', state.failureOffset);
            const data = await api(url);
            if (!append) state.failures.clear();
            data.failures.forEach((failure) => state.failures.set(failure.id, failure));
            state.failureOffset = data.next_offset;
            renderFailures();
        } finally { state.failuresBusy = false; el('more-failures').disabled = false; }
    }
    async function retryFailure(failure) {
        if (state.retryBusy) return;
        const reason = prompt('Motivo de la reactivación (5 a 500 caracteres). Revisa antes el error y la conversación:');
        if (reason === null) return;
        if (reason.trim().length < 5 || reason.trim().length > 500) { error('Indica un motivo de entre 5 y 500 caracteres.'); return; }
        if (!confirm(`¿Reactivar las partes locales fallidas desde la parte ${failure.part_number}? No se enviarán en esta petición. Un fallo de red anterior puede ocultar un envío aceptado y causar duplicados. Confirma que revisaste este riesgo.`)) return;
        state.retryBusy = true; error(); renderFailures();
        try {
            const data = await post(endpoint('retryUrl', failure.id), { reason: reason.trim(), confirm_risk: true });
            el('retry-status').textContent = `${data.parts} parte(s) reactivadas y auditadas. Pendientes de que el worker las envíe.`;
            await Promise.all([loadOperations(), loadFailures(), loadMessages()]);
        } catch (err) { error(err.message); }
        finally { state.retryBusy = false; renderFailures(); }
    }
    el('more-failures').addEventListener('click', () => loadFailures(true).catch((err) => error(err.message)));
    function renderContacts() {
        const list = el('contact-list');
        list.replaceChildren();
        state.contacts.forEach((contact) => {
            const li = document.createElement('li');
            const button = node('button', '', 'w-full rounded-lg border p-3 text-left hover:bg-blue-50');
            button.type = 'button';
            button.setAttribute('aria-pressed', String(state.contact?.id === contact.id));
            if (state.contact?.id === contact.id) button.classList.add('bg-blue-50', 'border-blue-400');
            button.append(node('span', contact.name || contact.phone, 'block break-words text-sm font-semibold'));
            button.append(node('span', `${contact.phone} · ${contact.mode === 'human' ? 'Humano' : 'IA'}`, 'block text-xs text-slate-500'));
            button.append(node('span', dateText(contact.last_message_at), 'block text-xs text-slate-400'));
            button.addEventListener('click', () => selectContact(contact));
            li.append(button);
            list.append(li);
        });
        el('contacts-status').textContent = state.contacts.size ? `${state.contacts.size} conversaciones cargadas` : 'No hay coincidencias.';
        el('more-contacts').classList.toggle('hidden', state.offset === null);
    }
    async function loadContacts(append = false) {
        if (append && state.listBusy) return;
        const generation = ++state.listGeneration;
        state.listBusy = true;
        el('more-contacts').disabled = true;
        const url = new URL(panel.dataset.conversationsUrl, location.origin);
        url.searchParams.set('q', state.query);
        if (append && state.offset !== null) url.searchParams.set('offset', state.offset);
        try {
            const data = await api(url);
            if (generation !== state.listGeneration) return;
            if (!append) state.contacts.clear();
            data.conversations.forEach((contact) => state.contacts.set(contact.id, contact));
            state.offset = data.next_offset;
            renderContacts();
        } finally {
            if (generation === state.listGeneration) {
                state.listBusy = false;
                el('more-contacts').disabled = false;
            }
        }
    }
    function renderMode() {
        const contact = state.contact;
        if (!contact) return;
        el('contact-title').textContent = contact.name || contact.phone;
        el('contact-phone').textContent = contact.phone;
        el('contact-mode').textContent = contact.mode === 'human' ? 'Humano' : 'IA';
        el('toggle-mode').textContent = contact.mode === 'human' ? 'Activar IA' : 'Tomar atención humana';
        el('toggle-mode').disabled = state.modeBusy;
        el('mode-note').textContent = contact.mode === 'human'
            ? 'Atención humana: no se generan nuevas respuestas automáticas. Los envíos ya encolados no se cancelan.'
            : 'IA activa: el bot puede responder. Toma la atención humana si vas a continuar la conversación manualmente.';
    }
    function deliveryText(message) {
        const sending = message.sending;
        if (!sending && message.status === 'pending' && message.retry_after_at) {
            return `En espera por límite · próximo intento: ${dateText(message.retry_after_at)}`;
        }
        if (!sending) return message.status === 'failed' ? 'Procesamiento fallido' : message.status === 'pending'
            ? 'Pendiente de procesar' : message.status === 'processing' ? 'Procesando' : 'Registrado';
        if (sending.failed_parts) return `Envío con fallos · ${sending.accepted_parts}/${sending.total_parts} partes aceptadas por Meta`;
        if (sending.read_parts === sending.total_parts) return `Leído · ${sending.total_parts} parte(s)`;
        if (sending.delivered_parts === sending.total_parts) return `Entregado · ${sending.read_parts}/${sending.total_parts} partes leídas`;
        if (sending.sent_parts === sending.total_parts) return `Enviado por WhatsApp · ${sending.delivered_parts}/${sending.total_parts} partes entregadas`;
        if (sending.accepted_parts === sending.total_parts) return `Aceptado por Meta · ${sending.delivered_parts}/${sending.total_parts} partes entregadas · ${sending.read_parts}/${sending.total_parts} leídas`;
        return `En cola · ${sending.accepted_parts}/${sending.total_parts} aceptadas · ${sending.delivered_parts}/${sending.total_parts} entregadas`;
    }
    function renderMessages() {
        const list = el('message-list');
        const existing = new Map([...list.children].map((item) => [Number(item.dataset.messageId), item]));
        const fragment = document.createDocumentFragment();
        const ordered = [...state.messages.values()].sort((a, b) => a.created_at.localeCompare(b.created_at) || a.id - b.id);
        ordered.forEach((message) => {
            const signature = JSON.stringify(message);
            if (existing.get(message.id)?.dataset.signature === signature) {
                fragment.append(existing.get(message.id));
                return;
            }
            const li = node('li', '', `max-w-[95%] rounded-xl border p-3 sm:max-w-[85%] ${message.role === 'assistant' ? 'ml-auto border-blue-100 bg-blue-50' : 'border-slate-200 bg-white'}`);
            li.dataset.messageId = message.id;
            li.dataset.signature = signature;
            li.append(node('p', `${message.role === 'user' ? 'Cliente' : 'Respuesta'} · ${dateText(message.created_at)}`, 'mb-2 text-xs text-slate-500'));
            if (message.content) li.append(node('p', message.content, 'whitespace-pre-wrap break-words text-sm [overflow-wrap:anywhere]'));
            if (message.media_url) {
                const button = node('button', 'Ver imagen', 'mt-2 rounded border bg-white px-3 py-1 text-sm text-blue-700');
                button.type = 'button';
                button.addEventListener('click', () => {
                    button.disabled = true;
                    button.textContent = 'Cargando imagen…';
                    const img = document.createElement('img');
                    img.alt = 'Imagen enviada por el cliente';
                    img.className = 'mt-2 max-h-80 max-w-full rounded-lg object-contain';
                    img.addEventListener('load', () => { button.remove(); });
                    img.addEventListener('error', () => {
                        img.remove(); button.disabled = false;
                        button.textContent = 'No disponible. Reintentar imagen';
                    });
                    img.src = message.media_url;
                    li.append(img);
                });
                li.append(button);
            }
            li.append(node('p', deliveryText(message), `mt-2 text-xs ${message.sending?.failed_parts || message.status === 'failed' ? 'text-red-700' : 'text-slate-500'}`));
            if (message.delivery_parts?.length) {
                const details = document.createElement('details');
                details.className = 'mt-2 text-xs text-slate-600';
                details.append(node('summary', 'Detalle de envío', 'cursor-pointer'));
                message.delivery_parts.forEach((part) => {
                    const labels = { accepted: 'Aceptada por Meta', sent: 'Enviada', delivered: 'Entregada', read: 'Leída',
                        failed: 'Fallida', pending: 'Pendiente', processing: 'Procesando' };
                    details.append(node('p', `Parte ${part.part_number}: ${labels[part.status] || part.status}. Intentos: ${part.attempts}.`, 'mt-1'));
                    if (part.next_attempt_at) details.append(node('p', `Próximo intento: ${dateText(part.next_attempt_at)}`));
                    if (part.sent_at) details.append(node('p', `Enviada: ${dateText(part.sent_at)}`));
                    if (part.delivered_at) details.append(node('p', `Entregada: ${dateText(part.delivered_at)}`));
                    if (part.read_at) details.append(node('p', `Leída: ${dateText(part.read_at)}`));
                    if (part.delivery_failed_at) details.append(node('p', `Fallo de entrega registrado: ${dateText(part.delivery_failed_at)}`));
                    if (part.last_error) details.append(node('p', part.last_error, 'break-words text-red-700'));
                });
                li.append(details);
            }
            fragment.append(li);
        });
        list.replaceChildren(fragment);
        el('messages-status').textContent = ordered.length ? '' : 'Esta conversación todavía no tiene mensajes.';
        el('older-messages').classList.toggle('hidden', !state.before);
    }
    async function loadMessages(older = false, initial = false) {
        if (!state.contact || state.messagesBusy || (older && !state.before)) return;
        state.messagesBusy = true;
        const id = state.contact.id;
        const generation = state.generation;
        const modeVersion = state.modeVersion;
        const url = new URL(endpoint('messagesUrl', id), location.origin);
        url.searchParams.set('limit', older || initial ? '50' : '500');
        if (older) { url.searchParams.set('before', state.before); state.olderBusy = true; }
        el('older-messages').disabled = state.olderBusy;
        const scroll = el('message-scroll');
        const previousHeight = scroll.scrollHeight;
        const previousTop = scroll.scrollTop;
        const nearBottom = scroll.scrollHeight - scroll.scrollTop - scroll.clientHeight < 100;
        try {
            const data = await api(url);
            if (generation !== state.generation) return;
            if (!state.modeBusy && modeVersion === state.modeVersion) state.contact = data.conversation;
            data.messages.forEach((message) => state.messages.set(message.id, message));
            if (older || initial) state.before = data.next_before;
            renderMode();
            renderMessages();
            if (older) scroll.scrollTop = previousTop + scroll.scrollHeight - previousHeight;
            else if (initial || nearBottom) scroll.scrollTop = scroll.scrollHeight;
            else scroll.scrollTop = previousTop;
        } catch (err) {
            if (generation === state.generation) throw err;
        } finally {
            if (generation === state.generation) {
                state.messagesBusy = false;
                state.olderBusy = false;
                el('older-messages').disabled = false;
            }
        }
    }
    async function selectContact(contact) {
        if (state.contact?.id === contact.id) return;
        if (state.sending || state.modeBusy) { error('Espera a que termine la operación actual.'); return; }
        if (el('manual-content').value && !confirm('¿Cambiar de conversación y descartar el borrador?')) return;
        ++state.generation;
        state.contact = contact;
        state.messages.clear(); state.before = null; state.olderBusy = false; state.messagesBusy = false;
        el('manual-content').value = '';
        el('manual-content').disabled = false;
        el('send-message').disabled = false;
        el('send-status').textContent = '';
        error(); renderContacts(); renderMode(); renderMessages();
        el('messages-status').textContent = 'Cargando historial…';
        try { await loadMessages(false, true); } catch (err) { error(err.message); }
    }
    el('contact-search').addEventListener('submit', async (event) => {
        event.preventDefault(); state.query = el('search-query').value.trim(); error();
        try { await loadContacts(); } catch (err) { error(err.message); }
    });
    el('more-contacts').addEventListener('click', () => loadContacts(true).catch((err) => error(err.message)));
    el('older-messages').addEventListener('click', () => loadMessages(true).catch((err) => error(err.message)));
    el('toggle-mode').addEventListener('click', async () => {
        if (!state.contact || state.modeBusy) return;
        state.modeBusy = true; renderMode(); error();
        ++state.modeVersion;
        const id = state.contact.id;
        const mode = state.contact.mode === 'human' ? 'ai' : 'human';
        try {
            const data = await post(endpoint('modeUrl', id), { mode });
            state.contact.mode = data.mode;
            if (state.contacts.has(id)) state.contacts.get(id).mode = data.mode;
            renderContacts();
        } catch (err) { error(err.message); }
        finally { state.modeBusy = false; renderMode(); }
    });
    el('message-form').addEventListener('submit', async (event) => {
        event.preventDefault();
        if (!state.contact || state.sending) return;
        const content = el('manual-content').value.trim();
        if (!content) return;
        state.sending = true; el('send-message').disabled = true; el('manual-content').disabled = true;
        error(); el('send-status').textContent = 'Encolando…';
        try {
            await post(endpoint('sendUrl', state.contact.id), { content });
            el('manual-content').value = '';
            el('send-status').textContent = 'Respuesta encolada, todavía no confirmada por Meta.';
            try { await loadMessages(); } catch (err) { error(`Se encoló la respuesta, pero no se pudo actualizar el historial: ${err.message}`); }
        } catch (err) { error(err.message); el('send-status').textContent = 'No se confirmó el encolado. Revisa el historial antes de reintentar.'; }
        finally { state.sending = false; el('send-message').disabled = false; el('manual-content').disabled = false; }
    });
    async function refresh() {
        if (state.refreshing || document.hidden || state.olderBusy || state.modeBusy || state.sending || state.retryBusy) return;
        state.refreshing = true;
        try {
            if (state.contacts.size <= 100 && !state.listBusy) await loadContacts();
            await loadMessages();
            await loadStatus();
            if (Date.now() - state.operationsAt >= 30000) {
                await loadOperations();
                if (state.failures.size <= 50) await loadFailures();
            }
        } catch (err) { error(err.message); }
        finally { state.refreshing = false; }
    }
    el('refresh-panel').addEventListener('click', async () => {
        error();
        try { await loadContacts(); await loadMessages(); await loadStatus(); await loadOperations(); await loadFailures(); } catch (err) { error(err.message); }
    });
    async function loadStatus() {
        const data = await api(panel.dataset.statusUrl);
        el('connection-status').textContent = data.configured
            ? 'Credenciales de Meta configuradas (no verifica conectividad).'
            : 'Meta sin configurar: revisa las credenciales antes de enviar.';
        const states = { not_started: 'No iniciado', stopped: 'Detenido', unresponsive: 'Sin latido reciente',
            stalled: 'Sin progreso', degraded: 'Con errores', running: 'Activo' };
        el('worker-status').textContent = `Worker: ${states[data.worker.state] || data.worker.state}. ${data.worker.healthy ? 'Procesamiento en segundo plano disponible.' : 'Revisa el servicio; los mensajes pueden quedar pendientes.'}`;
        el('worker-status').className = `text-sm ${data.worker.healthy ? 'text-green-700' : 'text-red-700'}`;
    }
    Promise.all([loadContacts(), loadStatus(), loadOperations(), loadFailures()]).catch((err) => error(err.message));
    setInterval(refresh, 10000);
})();
