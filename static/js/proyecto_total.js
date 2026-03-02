// proyecto_total.js — Toggle expand/collapse for project cards

document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.project-toggle').forEach(function (header) {
        header.addEventListener('click', function () {
            const chevron = header.querySelector('.chevron');
            const weeksDiv = header.closest('.project-card').querySelector('.project-weeks');

            if (chevron.classList.contains('open')) {
                chevron.classList.remove('open');
                weeksDiv.style.maxHeight = '0';
            } else {
                chevron.classList.add('open');
                weeksDiv.style.maxHeight = weeksDiv.scrollHeight + 'px';
            }
        });
    });
});
