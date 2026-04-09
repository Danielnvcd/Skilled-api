document.addEventListener('DOMContentLoaded', () => {
    const CSRF_TOKEN = document.querySelector('meta[name="csrf-token"]').getAttribute('content');
    let products = [];
    let basket = [];
    let selectedProduct = null;

    // Elementos DOM
    const productList = document.getElementById('productList');
    const basketItems = document.getElementById('basketItems');
    const basketCount = document.getElementById('basketCount');
    const searchInput = document.getElementById('searchProduct');
    const btnSubmit = document.getElementById('btnSubmitRequest');

    async function init() {
        await Promise.all([loadProducts(), loadProjects()]);
    }

    async function loadProducts() {
        try {
            const res = await fetch('/api/v1/productos/');
            products = await res.json();
            renderProducts(products);
        } catch(e) {
            productList.innerHTML = '<p class="col-span-full text-center text-red-400 py-8">Error cargando productos</p>';
        }
    }

    async function loadProjects() {
        const sel = document.getElementById('selProyecto');
        try {
            // Buscamos un endpoint que nos de los proyectos
            const res = await fetch('/proyectos/'); 
            // Fallback manual si falla carga dinámica compleja
            const opt = document.createElement('option');
            opt.value = "General / Oficina"; opt.textContent = "General / Oficina";
            sel.appendChild(opt);
        } catch(e) {
            console.warn("No se pudieron cargar proyectos dinámicos");
        }
    }

    function renderProducts(list) {
        if(!list.length) {
            productList.innerHTML = '<div class="col-span-full py-12 text-center text-gray-400">No se encontraron productos</div>';
            return;
        }
        productList.innerHTML = list.map(p => `
            <div class="product-item cursor-pointer" data-id="${p.id}">
                <div class="flex-1">
                    <p class="font-bold text-gray-800 text-xs truncate mb-1">${p.descripcion}</p>
                    <div class="flex gap-2">
                        <span class="text-[9px] font-black bg-gray-100 px-1.5 py-0.5 rounded text-gray-500 uppercase">${p.codigo}</span>
                        <span class="text-[9px] font-bold text-indigo-400">${p.categoria}</span>
                    </div>
                </div>
                <div class="shrink-0 ml-2">
                    <span class="text-[10px] font-bold text-emerald-500 bg-emerald-50 px-2 py-1 rounded-lg">
                        ${p.stock_actual} ${p.unidad}
                    </span>
                </div>
            </div>
        `).join('');

        // Agregar eventos vía JS para evitar inline 'onclick' (CSP)
        productList.querySelectorAll('.product-item').forEach(el => {
            el.addEventListener('click', () => {
                const id = parseInt(el.getAttribute('data-id'));
                openQtyModal(id);
            });
        });
    }

    searchInput.addEventListener('input', (e) => {
        const q = e.target.value.toLowerCase();
        const filtered = products.filter(p => 
            p.descripcion.toLowerCase().includes(q) || 
            p.codigo.toLowerCase().includes(q)
        );
        renderProducts(filtered);
    });

    function openQtyModal(id) {
        selectedProduct = products.find(p => p.id === id);
        document.getElementById('modalProdName').textContent = selectedProduct.descripcion;
        document.getElementById('modalProdSku').textContent = selectedProduct.codigo;
        document.getElementById('modalProdUnit').textContent = selectedProduct.unidad;
        document.getElementById('inputQty').value = 1;
        document.getElementById('modalQty').classList.remove('hidden');
    }

    document.getElementById('btnCancelQty').onclick = () => {
        document.getElementById('modalQty').classList.add('hidden');
    };

    document.getElementById('btnAddConfirm').onclick = () => {
        const qty = parseFloat(document.getElementById('inputQty').value);
        if(qty <= 0 || isNaN(qty)) return;

        const exists = basket.find(item => item.id === selectedProduct.id);
        if(exists) {
            exists.cantidad += qty;
        } else {
            basket.push({
                id: selectedProduct.id,
                codigo: selectedProduct.codigo,
                descripcion: selectedProduct.descripcion,
                unidad: selectedProduct.unidad,
                cantidad: qty
            });
        }

        updateBasket();
        document.getElementById('modalQty').classList.add('hidden');
    };

    function updateBasket() {
        basketCount.textContent = `${basket.length} Items`;
        btnSubmit.disabled = basket.length === 0;

        if(!basket.length) {
            basketItems.innerHTML = '<div class="text-center py-8 border-2 border-dashed border-gray-50 rounded-2xl"><p class="text-gray-400 text-sm font-medium">No has agregado nada aún</p></div>';
            return;
        }

        basketItems.innerHTML = basket.map((item, index) => `
            <div class="basket-item bg-gray-50/50 p-3 rounded-xl border border-gray-100/50 flex items-center justify-between">
                <div class="min-w-0 flex-1 pr-3">
                    <p class="text-[11px] font-bold text-gray-800 truncate">${item.descripcion}</p>
                    <p class="text-[9px] font-black text-gray-400 uppercase mt-0.5">${item.codigo}</p>
                </div>
                <div class="flex items-center gap-3">
                    <div class="text-right">
                        <span class="text-sm font-black text-indigo-600">${item.cantidad}</span>
                        <span class="text-[9px] font-bold text-gray-400 uppercase ml-0.5">${item.unidad}</span>
                    </div>
                    <button class="btn-remove-basket text-gray-300 hover:text-rose-500 transition-colors p-1" data-index="${index}">
                        <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3"><path d="M18 6 6 18M6 6l12 12"/></svg>
                    </button>
                </div>
            </div>
        `).join('');

        basketItems.querySelectorAll('.btn-remove-basket').forEach(btn => {
            btn.onclick = () => {
                const index = parseInt(btn.getAttribute('data-index'));
                basket.splice(index, 1);
                updateBasket();
            };
        });
    }

    btnSubmit.onclick = async () => {
        const proyecto = document.getElementById('selProyecto').value;
        if(!proyecto) {
            alert("Por favor selecciona o escribe un proyecto.");
            return;
        }

        const data = {
            proyecto: proyecto,
            detalles: basket.map(item => ({
                producto_id: item.id,
                cantidad_solicitada: item.cantidad
            }))
        };

        try {
            btnSubmit.disabled = true;
            btnSubmit.innerHTML = '<span class="inline-block animate-spin mr-2">↻</span> Enviando...';
            
            const res = await fetch('/api/v1/solicitudes/', {
                method: 'POST',
                headers: { 
                    'Content-Type': 'application/json',
                    'X-CSRF-Token': CSRF_TOKEN
                },
                body: JSON.stringify(data)
            });

            if(res.ok) {
                alert("¡Solicitud enviada con éxito!");
                basket = [];
                updateBasket();
                document.getElementById('selProyecto').value = "";
            } else {
                const err = await res.json();
                alert("Error: " + (err.detail || "No se pudo enviar la solicitud"));
            }
        } catch(e) {
            alert("Error de red");
        } finally {
            btnSubmit.disabled = basket.length === 0;
            btnSubmit.textContent = "Enviar Solicitud";
        }
    };

    init();
});
