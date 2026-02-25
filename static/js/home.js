(function () {
    // Colors Palette
    const colors = [
        '#3b82f6', '#10b981', '#f59e0b', '#8b5cf6', '#ef4444',
        '#06b6d4', '#f97316', '#6366f1', '#14b8a6', '#db2777'
    ];

    // Chart 1: Proyectos (Doughnut)
    const ctxProyectos = document.getElementById('chartProyectos');
    if (ctxProyectos) {
        const labelsData = JSON.parse(ctxProyectos.getAttribute('data-labels') || '[]');
        const valuesData = JSON.parse(ctxProyectos.getAttribute('data-values') || '[]');

        new Chart(ctxProyectos, {
            type: 'doughnut',
            data: {
                labels: labelsData,
                datasets: [{
                    data: valuesData,
                    backgroundColor: colors,
                    borderWidth: 2,
                    hoverOffset: 4
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: {
                        position: 'right',
                        labels: {
                            boxWidth: 12,
                            padding: 15,
                            font: { size: 11 }
                        }
                    }
                },
                cutout: '70%'
            }
        });
    }

    // Chart 2: Puestos (Bar)
    const ctxPuestos = document.getElementById('chartPuestos');
    if (ctxPuestos) {
        const labelsData = JSON.parse(ctxPuestos.getAttribute('data-labels') || '[]');
        const valuesData = JSON.parse(ctxPuestos.getAttribute('data-values') || '[]');

        new Chart(ctxPuestos, {
            type: 'bar',
            data: {
                labels: labelsData,
                datasets: [{
                    label: 'Cantidad de Empleados',
                    data: valuesData,
                    backgroundColor: colors,
                    borderRadius: 4
                }]
            },
            options: {
                responsive: true,
                maintainAspectRatio: false,
                plugins: {
                    legend: { display: false }
                },
                scales: {
                    y: {
                        beginAtZero: true,
                        ticks: { stepSize: 1 }
                    },
                    x: {
                        grid: { display: false }
                    }
                }
            }
        });
    }
})();
