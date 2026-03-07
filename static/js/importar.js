document.addEventListener('DOMContentLoaded', () => {
    const fileInput = document.getElementById('archivoExcel');
    const statusDiv = document.getElementById('fileUploadStatus');
    const nameDisplay = document.getElementById('fileNameDisplay');
    const uploadArea = document.querySelector('.upload-area');

    if (fileInput) {
        fileInput.addEventListener('change', function () {
            if (this.files && this.files.length > 0) {
                const fileName = this.files[0].name;
                nameDisplay.textContent = fileName + ' listo para subir';
                statusDiv.style.display = 'flex';

                // Darle un toque visual de éxito a la caja
                if (uploadArea) {
                    uploadArea.style.borderColor = '#10B981';
                    uploadArea.style.backgroundColor = '#ecfdf5';
                }
            } else {
                statusDiv.style.display = 'none';
                if (uploadArea) {
                    uploadArea.style.borderColor = '';
                    uploadArea.style.backgroundColor = '';
                }
            }
        });
    }
});
