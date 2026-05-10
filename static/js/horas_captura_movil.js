// horas_captura_movil.js v=1
(function () {
    var cfg        = JSON.parse(document.getElementById('cap-config').textContent);
    var regsData   = JSON.parse(document.getElementById('cap-registros').textContent);
    var semana     = JSON.parse(document.getElementById('cap-semana').textContent);
    var incidencias = JSON.parse(document.getElementById('cap-incidencias').textContent);

    var BORRADOR   = cfg.borrador;
    var REPORTE_ID = cfg.reporte_id;

    function getCSRF() {
        var el = document.querySelector('input[name="csrf_token"]');
        return el ? el.value : '';
    }

    function showToast(msg) {
        var t = document.getElementById('toast');
        t.textContent = msg;
        t.classList.add('show');
        setTimeout(function () { t.classList.remove('show'); }, 3000);
    }

    function regDe(trabajadorId, fecha) {
        return regsData.find(function (r) {
            return r.trabajador_id === trabajadorId && r.fecha === fecha;
        }) || null;
    }

    // ── Stats badge en header de trabajador ──────────────────────────────────
    function actualizarStats(trabajadorId) {
        var regs = regsData.filter(function (r) { return r.trabajador_id === trabajadorId; });
        var dias = regs.length;
        var total = regs.reduce(function (s, r) { return s + (r.horas_productivas || 0); }, 0);
        var el = document.getElementById('stats-' + trabajadorId);
        if (!el) return;
        if (dias > 0) {
            el.innerHTML = '<span class="stats-dias">' + dias + '/' + semana.length + ' días</span>'
                + (total > 0 ? '<br>' + total.toFixed(1) + 'h' : '');
        } else {
            el.innerHTML = '<span style="color:#cbd5e1;">' + semana.length + ' días</span>';
        }
    }

    // ── Construir select de incidencias ──────────────────────────────────────
    function makeIncSelect(cls, valor, disabled) {
        var sel = document.createElement('select');
        sel.className = 'inc-select ' + (cls || '');
        sel.disabled = disabled;
        var opt0 = document.createElement('option');
        opt0.value = '';
        opt0.textContent = 'Sin Incidencia';
        sel.appendChild(opt0);
        incidencias.forEach(function (inc) {
            var o = document.createElement('option');
            o.value = inc;
            o.textContent = inc;
            sel.appendChild(o);
        });
        sel.value = valor || '';
        return sel;
    }

    // ── Construir fila de día ────────────────────────────────────────────────
    function crearFilaDia(fechaObj, trabajadorId) {
        var fecha = fechaObj.fecha;
        var reg   = regDe(trabajadorId, fecha);

        var div = document.createElement('div');
        div.className = 'day-row';
        div.dataset.fecha = fecha;
        div.dataset.registroId = reg ? String(reg.id) : '';

        // Etiqueta día
        var lbl = document.createElement('div');
        lbl.className = 'day-label' + (fechaObj.fin_semana ? ' fin-semana' : '');
        lbl.textContent = fechaObj.dia + ' ' + fechaObj.label;
        div.appendChild(lbl);

        // Status feedback
        var statusDiv = document.createElement('div');
        statusDiv.className = 'day-status';
        div.appendChild(statusDiv);

        // Inputs de tiempo
        var gridDiv = document.createElement('div');
        gridDiv.className = 'day-inputs';

        function timeField(labelTxt, cls, valor) {
            var wrap = document.createElement('div');
            wrap.className = 'time-field';
            var l = document.createElement('label');
            l.textContent = labelTxt;
            var inp = document.createElement('input');
            inp.type = 'time';
            inp.className = cls;
            inp.value = valor || '';
            inp.disabled = !BORRADOR;
            wrap.appendChild(l);
            wrap.appendChild(inp);
            return { wrap: wrap, inp: inp };
        }

        var entradaF = timeField('Entrada', 'inp-entrada', reg ? reg.hora_entrada : '');
        var salidaF  = timeField('Salida',  'inp-salida',  reg ? reg.hora_salida  : '');
        gridDiv.appendChild(entradaF.wrap);
        gridDiv.appendChild(salidaF.wrap);
        div.appendChild(gridDiv);

        // Comida + Incidencia
        var extrasDiv = document.createElement('div');
        extrasDiv.className = 'day-extras';

        var chkLabel = document.createElement('label');
        chkLabel.className = 'check-label';
        var chkComida = document.createElement('input');
        chkComida.type = 'checkbox';
        chkComida.checked = reg ? reg.tomo_comida : false;
        chkComida.disabled = !BORRADOR;
        chkLabel.appendChild(chkComida);
        chkLabel.appendChild(document.createTextNode('Comida'));
        extrasDiv.appendChild(chkLabel);

        var selInc = makeIncSelect('', reg ? reg.incidencia : '', !BORRADOR);
        extrasDiv.appendChild(selInc);
        div.appendChild(extrasDiv);

        // Botones
        if (BORRADOR) {
            var actionsDiv = document.createElement('div');
            actionsDiv.className = 'day-actions';

            var btnG = document.createElement('button');
            btnG.type = 'button';
            btnG.className = 'btn-guardar';
            btnG.textContent = reg ? 'Actualizar' : 'Guardar';

            btnG.addEventListener('click', function () {
                guardarDia(div, trabajadorId, {
                    inpE: entradaF.inp, inpS: salidaF.inp,
                    chkC: chkComida, selI: selInc,
                    statusDiv: statusDiv, btnG: btnG
                });
            });
            actionsDiv.appendChild(btnG);

            if (reg) {
                var btnDel = crearBtnEliminar(div, reg.id, trabajadorId, statusDiv);
                actionsDiv.appendChild(btnDel);
            }
            div.appendChild(actionsDiv);
        } else if (reg) {
            var cerradoDiv = document.createElement('div');
            cerradoDiv.style.cssText = 'font-size:0.75rem; color:#94a3b8; padding:4px 0;';
            cerradoDiv.textContent = 'Reporte cerrado';
            div.appendChild(cerradoDiv);
        }

        return div;
    }

    function crearBtnEliminar(rowDiv, regId, trabajadorId, statusDiv) {
        var btn = document.createElement('button');
        btn.type = 'button';
        btn.className = 'btn-eliminar';
        btn.textContent = 'Eliminar';
        btn.addEventListener('click', function () {
            if (!confirm('¿Eliminar este registro?')) return;
            btn.disabled = true;
            var fd = new FormData();
            fd.append('csrf_token', getCSRF());
            fetch('/horas/eliminar_registro/' + regId, {
                method: 'POST', body: fd,
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            }).then(function (r) { return r.json(); }).then(function (data) {
                if (data.ok) {
                    regsData = regsData.filter(function (r) { return r.id !== regId; });
                    rowDiv.dataset.registroId = '';
                    statusDiv.style.color = '#94a3b8';
                    statusDiv.textContent = 'Eliminado';
                    // reset inputs
                    rowDiv.querySelector('.inp-entrada').value = '';
                    rowDiv.querySelector('.inp-salida').value = '';
                    rowDiv.querySelector('input[type="checkbox"]').checked = false;
                    rowDiv.querySelector('.inc-select').value = '';
                    // replace Actualizar/Eliminar with Guardar only
                    var actDiv = rowDiv.querySelector('.day-actions');
                    var oldBtnG = actDiv.querySelector('.btn-guardar');
                    oldBtnG.textContent = 'Guardar';
                    btn.remove();
                    actualizarStats(trabajadorId);
                } else {
                    statusDiv.style.color = '#dc2626';
                    statusDiv.textContent = data.error || 'Error';
                    btn.disabled = false;
                }
            }).catch(function () {
                statusDiv.style.color = '#dc2626';
                statusDiv.textContent = 'Error de red';
                btn.disabled = false;
            });
        });
        return btn;
    }

    // ── Guardar día ──────────────────────────────────────────────────────────
    async function guardarDia(rowDiv, trabajadorId, f) {
        var registroId = rowDiv.dataset.registroId;
        var fecha      = rowDiv.dataset.fecha;
        var entrada    = f.inpE.value;
        var salida     = f.inpS.value;
        var incidencia = f.selI.value;

        if (!registroId && !entrada && !salida && !incidencia) {
            f.statusDiv.style.color = '#94a3b8';
            f.statusDiv.textContent = 'Sin datos';
            return;
        }

        var url = registroId
            ? '/horas/editar_registro/' + registroId
            : '/horas/guardar_registro/' + REPORTE_ID;

        var fd = new FormData();
        fd.append('csrf_token', getCSRF());
        fd.append('hora_entrada', entrada);
        fd.append('hora_salida', salida);
        if (f.chkC.checked) fd.append('tomo_comida', '1');
        fd.append('incidencia', incidencia);
        if (!registroId) {
            fd.append('trabajador_id', trabajadorId);
            fd.append('fecha', fecha);
        }

        f.btnG.disabled = true;
        f.statusDiv.textContent = '';

        try {
            var resp = await fetch(url, {
                method: 'POST', body: fd,
                headers: { 'X-Requested-With': 'XMLHttpRequest' }
            });
            var data = await resp.json();

            if (data.ok) {
                f.statusDiv.style.color = '#16a34a';
                f.statusDiv.textContent = '✓ Guardado';
                f.btnG.textContent = 'Actualizar';

                var idx = regsData.findIndex(function (r) { return r.id === data.registro.id; });
                if (idx >= 0) {
                    regsData[idx] = Object.assign(regsData[idx], data.registro);
                } else {
                    regsData.push(Object.assign({ trabajador_id: trabajadorId, fecha: fecha }, data.registro));
                }
                rowDiv.dataset.registroId = String(data.registro.id);

                // Add Eliminar button if it's a new record
                var actDiv = rowDiv.querySelector('.day-actions');
                if (actDiv && !actDiv.querySelector('.btn-eliminar')) {
                    var btnDel = crearBtnEliminar(rowDiv, data.registro.id, trabajadorId, f.statusDiv);
                    actDiv.appendChild(btnDel);
                }

                actualizarStats(trabajadorId);
                setTimeout(function () { f.statusDiv.textContent = ''; }, 2500);
            } else {
                f.statusDiv.style.color = '#dc2626';
                f.statusDiv.textContent = data.error || 'Error al guardar';
            }
        } catch (e) {
            f.statusDiv.style.color = '#dc2626';
            f.statusDiv.textContent = 'Error de red';
        }

        f.btnG.disabled = false;
    }

    // ── Inicializar stats y acordeón ─────────────────────────────────────────
    document.querySelectorAll('.worker-cap-toggle').forEach(function (btn) {
        var trabajadorId = parseInt(btn.dataset.trabajadorId, 10);
        actualizarStats(trabajadorId);

        btn.addEventListener('click', function () {
            var bodyId = btn.dataset.target;
            var body   = document.getElementById(bodyId);
            var chevron = btn.querySelector('.cap-chevron');
            var isOpen  = btn.getAttribute('aria-expanded') === 'true';

            btn.setAttribute('aria-expanded', String(!isOpen));
            body.style.display = isOpen ? 'none' : 'block';
            chevron.style.transform = isOpen ? '' : 'rotate(180deg)';

            // Lazy build day rows on first open
            if (!isOpen && body.dataset.loaded === 'false') {
                body.dataset.loaded = 'true';
                semana.forEach(function (fechaObj) {
                    body.appendChild(crearFilaDia(fechaObj, trabajadorId));
                });
            }
        });
    });
})();
