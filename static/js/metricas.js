/**
 * Métricas JS - v48 - Sincronización final de pestañas y paneles
 */
(function() {
    console.log("Metricas script cargado v48");

    function initMetricas() {
        try {
            const dataContainer = document.getElementById('metricasData');
            if (!dataContainer) {
                console.error('Error: Contenedor #metricasData no encontrado.');
                return;
            }

            // Lectura de datos desde atributos data-*
            const chartData = {
                plantas: {
                    labels: JSON.parse(dataContainer.getAttribute('data-plantas-labels') || '[]'),
                    counts: JSON.parse(dataContainer.getAttribute('data-plantas-datos') || '[]').map(n => Number(n) || 0)
                },
                permisos: {
                    registrados: Number(dataContainer.getAttribute('data-dc3-con')) || 0,
                    faltantes: Number(dataContainer.getAttribute('data-dc3-sin')) || 0
                }
            };

            const plantColors = ['#3B82F6', '#10B981', '#8B5CF6', '#F59E0B', '#EF4444'];
            let barChart, doughnutChart;

            function renderCharts() {
                try {
                    const isDark = !document.documentElement.classList.contains('light-theme');
                    const textColor = isDark ? '#F8FAFC' : '#475569';
                    const gridColor = isDark ? 'rgba(255, 255, 255, 0.08)' : 'rgba(0, 0, 0, 0.08)';

                    console.log("Renderizando gráficas v48 - Oscuro:", !isDark);

                    if (barChart) barChart.destroy();
                    if (doughnutChart) doughnutChart.destroy();

                    // Gráfica de Barras (Empleados por Planta)
                    const ctxBar = document.getElementById('chartPlantas');
                    if (ctxBar && typeof Chart !== 'undefined') {
                        barChart = new Chart(ctxBar, {
                            type: 'bar',
                            data: {
                                labels: chartData.plantas.labels,
                                datasets: [{
                                    label: 'Trabajadores',
                                    data: chartData.plantas.counts,
                                    backgroundColor: plantColors,
                                    borderRadius: 6
                                }]
                            },
                            options: {
                                responsive: true,
                                maintainAspectRatio: false,
                                plugins: { 
                                    legend: { display: false },
                                    tooltip: {
                                        backgroundColor: isDark ? '#1e293b' : '#fff',
                                        titleColor: isDark ? '#fff' : '#1e293b',
                                        bodyColor: isDark ? '#fff' : '#1e293b'
                                    }
                                },
                                scales: {
                                    y: { 
                                        beginAtZero: true,
                                        ticks: { color: textColor, precision: 0 }, 
                                        grid: { color: gridColor, drawBorder: false } 
                                    },
                                    x: { 
                                        ticks: { color: textColor }, 
                                        grid: { display: false } 
                                    }
                                }
                            }
                        });
                    }

                    // Gráfica de Dona (DC3)
                    const ctxDoughnut = document.getElementById('chartDC3');
                    if (ctxDoughnut && typeof Chart !== 'undefined') {
                        const total = chartData.permisos.registrados + chartData.permisos.faltantes;
                        doughnutChart = new Chart(ctxDoughnut, {
                            type: 'doughnut',
                            data: {
                                labels: [`Con DC3 (${chartData.permisos.registrados})`, `Sin DC3 (${chartData.permisos.faltantes})`],
                                datasets: [{
                                    data: [chartData.permisos.registrados, chartData.permisos.faltantes],
                                    backgroundColor: ['#10B981', isDark ? '#334155' : '#F1F5F9'],
                                    borderColor: isDark ? '#1e293b' : '#fff',
                                    borderWidth: 4
                                }]
                            },
                            options: {
                                responsive: true,
                                maintainAspectRatio: false,
                                cutout: '75%',
                                plugins: {
                                    legend: { 
                                        position: 'bottom', 
                                        labels: { color: textColor, usePointStyle: true, padding: 15 } 
                                    }
                                }
                            }
                        });
                    }
                } catch (err) {
                    console.error("Error en renderCharts:", err);
                }
            }

            // --- Lógica de Pestañas (Corregida para usar atributos data) ---
            const plantTabs = document.querySelectorAll('.plant-tab');
            const plantPanels = document.querySelectorAll('.plant-panel');

            if (plantTabs.length > 0) {
                plantTabs.forEach(tab => {
                    tab.addEventListener('click', function() {
                        const targetPlantName = this.getAttribute('data-plant');
                        console.log("Cambiando a planta:", targetPlantName);
                        
                        // Quitar activo de todos los botones y paneles
                        plantTabs.forEach(t => t.classList.remove('active'));
                        plantPanels.forEach(p => p.classList.remove('active'));
                        
                        // Activar el botón clicado
                        this.classList.add('active');
                        
                        // Activar el panel correspondiente usando el atributo data-plant-panel
                        const targetPanel = document.querySelector(`.plant-panel[data-plant-panel="${targetPlantName}"]`);
                        if (targetPanel) {
                            targetPanel.classList.add('active');
                        } else {
                            console.warn(`No se encontró el panel para la planta: ${targetPlantName}`);
                        }
                    });
                });
            }

            // Ejecución inicial
            renderCharts();

            window.addEventListener('themeChanged', () => {
                renderCharts();
            });

        } catch (err) {
            console.error("Error en initMetricas:", err);
        }
    }

    // Asegurar ejecución independientemente del estado de carga
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', initMetricas);
    } else {
        initMetricas();
    }
})();
