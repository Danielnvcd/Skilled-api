document.addEventListener('DOMContentLoaded', function () {

    const dataEl = document.getElementById('metricasData');
    if (!dataEl) return;

    const data = {
        plantasLabels: JSON.parse(dataEl.getAttribute('data-plantas-labels') || '[]'),
        plantasDatos: JSON.parse(dataEl.getAttribute('data-plantas-datos') || '[]'),
        dc3Con: parseInt(dataEl.getAttribute('data-dc3-con') || '0', 10),
        dc3Sin: parseInt(dataEl.getAttribute('data-dc3-sin') || '0', 10)
    };

    // Color palette
    const plantColors = [
        '#3b82f6', '#10b981', '#8b5cf6', '#f59e0b', '#ef4444',
        '#06b6d4', '#ec4899', '#14b8a6', '#f97316', '#6366f1',
        '#84cc16', '#a855f7'
    ];

    // === Bar Chart: Employees by Plant ===
    const ctxPlantas = document.getElementById('chartPlantas');
    if (ctxPlantas && data.plantasLabels.length > 0) {
        new Chart(ctxPlantas, {
            type: 'bar',
            data: {
                labels: data.plantasLabels,
                datasets: [{
                    label: 'Empleados',
                    data: data.plantasDatos,
                    backgroundColor: data.plantasLabels.map((_, i) => plantColors[i % plantColors.length] + 'cc'),
                    borderColor: data.plantasLabels.map((_, i) => plantColors[i % plantColors.length]),
                    borderWidth: 2,
                    borderRadius: 8,
                    borderSkipped: false,
                    barPercentage: 0.5,
                    categoryPercentage: 0.6
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false },
                    tooltip: {
                        backgroundColor: '#1f2937',
                        titleFont: { size: 13, weight: '600' },
                        bodyFont: { size: 12 },
                        padding: 12,
                        cornerRadius: 8,
                        callbacks: {
                            label: function (ctx) {
                                return `  ${ctx.parsed.y} empleado${ctx.parsed.y !== 1 ? 's' : ''}`;
                            }
                        }
                    }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: {
                            stepSize: 1,
                            color: '#94a3b8',
                            font: { size: 12 }
                        },
                        grid: {
                            color: '#f1f5f9'
                        }
                    },
                    x: {
                        ticks: {
                            color: '#475569',
                            font: { size: 12, weight: '600' }
                        },
                        grid: {
                            display: false
                        }
                    }
                }
            }
        });
    }

    // === Doughnut Chart: DC3 ===
    const ctxDC3 = document.getElementById('chartDC3');
    if (ctxDC3) {
        new Chart(ctxDC3, {
            type: 'doughnut',
            data: {
                labels: ['Con DC3', 'Sin DC3'],
                datasets: [{
                    data: [data.dc3Con, data.dc3Sin],
                    backgroundColor: ['#10b981cc', '#e5e7ebcc'],
                    borderColor: ['#10b981', '#d1d5db'],
                    borderWidth: 2,
                    hoverOffset: 8
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                cutout: '65%',
                plugins: {
                    legend: {
                        position: 'bottom',
                        labels: {
                            padding: 20,
                            usePointStyle: true,
                            pointStyleWidth: 12,
                            font: { size: 13, weight: '500' },
                            color: '#374151'
                        }
                    },
                    tooltip: {
                        backgroundColor: '#1f2937',
                        titleFont: { size: 13, weight: '600' },
                        bodyFont: { size: 12 },
                        padding: 12,
                        cornerRadius: 8,
                        callbacks: {
                            label: function (ctx) {
                                const total = data.dc3Con + data.dc3Sin;
                                const pct = total > 0 ? Math.round(ctx.parsed / total * 100) : 0;
                                return `  ${ctx.label}: ${ctx.parsed} (${pct}%)`;
                            }
                        }
                    }
                }
            }
        });
    }

    // === Plant Tabs ===
    const tabs = document.querySelectorAll('.plant-tab');
    const panels = document.querySelectorAll('.plant-panel');

    tabs.forEach(tab => {
        tab.addEventListener('click', function () {
            const plant = this.getAttribute('data-plant');

            // Toggle active tab
            tabs.forEach(t => t.classList.remove('active'));
            this.classList.add('active');

            // Toggle active panel
            panels.forEach(p => p.classList.remove('active'));
            const targetPanel = document.querySelector(`[data-plant-panel="${plant}"]`);
            if (targetPanel) targetPanel.classList.add('active');
        });
    });

});
