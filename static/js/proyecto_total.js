document.addEventListener('DOMContentLoaded', function () {
    document.querySelectorAll('.project-toggle').forEach(function (header) {
        header.addEventListener('click', function () {
            const chevron = header.querySelector('.pt-chevron');
            const body    = header.closest('.pt-card').querySelector('.pt-body');

            if (chevron.classList.contains('open')) {
                chevron.classList.remove('open');
                body.style.maxHeight = '0';
            } else {
                chevron.classList.add('open');
                body.style.maxHeight = body.scrollHeight + 'px';
            }
        });
    });
});
