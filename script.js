const API_BASE_URL = 'http://localhost:5000/api';
let currentQRData = '';
let isQRValidated = false;
let currentAadhaarNumber = '';
let webcamStream = null;
let scanInterval = null;

// Page Navigation
function showPage(pageId) {
    document.querySelectorAll('.page').forEach(page => {
        page.classList.remove('active');
    });
    document.getElementById(pageId).classList.add('active');
}

// Password Toggle
function togglePassword(inputId) {
    const input = document.getElementById(inputId);
    const button = input.nextElementSibling;
    const icon = button.querySelector('i');
    
    if (input.type === 'password') {
        input.type = 'text';
        icon.classList.remove('fa-eye');
        icon.classList.add('fa-eye-slash');
    } else {
        input.type = 'password';
        icon.classList.remove('fa-eye-slash');
        icon.classList.add('fa-eye');
    }
}

// Modal Functions
function showModal(modalId, show = true) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.style.display = show ? 'flex' : 'none';
        if (modalId === 'webcam-modal' && show) {
            startModalWebcam();
        }
    }
}

function closeWebcamModal() {
    stopModalWebcam();
    showModal('webcam-modal', false);
}

function showMessage(type, title, message) {
    const icon = document.getElementById('message-icon');
    if (type === 'success') {
        icon.innerHTML = '<i class="fas fa-check-circle" style="color: #28a745; font-size: 50px;"></i>';
    } else {
        icon.innerHTML = '<i class="fas fa-times-circle" style="color: #dc3545; font-size: 50px;"></i>';
    }
    
    document.getElementById('message-title').textContent = title;
    document.getElementById('message-content').textContent = message;
    document.getElementById('message-close').onclick = () => showModal('message-modal', false);
    showModal('message-modal', true);
}

// API Functions
async function apiCall(endpoint, options = {}) {
    const url = `${API_BASE_URL}${endpoint}`;
    
    const defaultOptions = {
        headers: {
            'Accept': 'application/json',
        },
        ...options
    };
    
    if (options.body && typeof options.body === 'string') {
        defaultOptions.headers['Content-Type'] = 'application/json';
    }
    
    try {
        const response = await fetch(url, defaultOptions);
        const contentType = response.headers.get('content-type');
        let data;
        
        if (contentType && contentType.includes('application/json')) {
            data = await response.json();
        } else {
            data = { message: await response.text() };
        }
        
        return { response, data };
    } catch (error) {
        console.error('API error:', error);
        throw error;
    }
}

// Login
document.getElementById('login-form').addEventListener('submit', async function(e) {
    e.preventDefault();
    
    const username = document.getElementById('username').value.trim();
    const password = document.getElementById('password').value.trim();
    
    if (!username || !password) {
        showMessage('error', 'Error', 'Enter username and password');
        return;
    }
    
    showModal('loading-modal', true);
    document.getElementById('loading-text').textContent = 'Logging in...';
    
    try {
        const { response, data } = await apiCall('/login', {
            method: 'POST',
            body: JSON.stringify({ username, password })
        });
        
        showModal('loading-modal', false);
        
        if (data.success) {
            localStorage.setItem('username', username);
            localStorage.setItem('full_name', data.user.full_name);
            localStorage.setItem('aadhaar_number', data.user.aadhaar_number || '');
            localStorage.setItem('is_verified', data.user.is_verified || 'false');
            
            updateDashboardInfo(data.user);
            showPage('dashboard-page');
            showMessage('success', 'Success', 'Login successful');
        } else {
            showMessage('error', 'Login Failed', data.message || 'Invalid credentials');
        }
    } catch (error) {
        showModal('loading-modal', false);
        showMessage('error', 'Connection Error', 'Failed to connect to server');
    }
});

// Registration QR Scanning Functions
async function startWebcamScan() {
    try {
        const video = document.getElementById('webcam-video');
        const constraints = {
            video: {
                width: { ideal: 1280 },
                height: { ideal: 720 },
                facingMode: 'environment'
            }
        };
        
        webcamStream = await navigator.mediaDevices.getUserMedia(constraints);
        video.srcObject = webcamStream;
        
        document.getElementById('webcam-container').style.display = 'block';
        document.getElementById('webcam-status').textContent = 'Camera active. Position QR code in view.';
    } catch (error) {
        console.error('Webcam error:', error);
        showMessage('error', 'Camera Error', 'Cannot access webcam. Please check permissions.');
    }
}

function stopWebcamScan() {
    if (webcamStream) {
        webcamStream.getTracks().forEach(track => track.stop());
        webcamStream = null;
    }
    
    const video = document.getElementById('webcam-video');
    if (video) {
        video.srcObject = null;
    }
    
    document.getElementById('webcam-container').style.display = 'none';
}

async function captureQRFromWebcam() {
    const video = document.getElementById('webcam-video');
    const canvas = document.getElementById('webcam-canvas');
    
    if (!video || !canvas) return;
    
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    
    // Convert canvas to blob
    canvas.toBlob(async (blob) => {
        await processImageBlob(blob, 'webcam');
    }, 'image/jpeg', 0.8);
}

function handleImageUpload(input) {
    if (input.files && input.files[0]) {
        const file = input.files[0];
        const reader = new FileReader();
        
        reader.onload = function(e) {
            const preview = document.getElementById('uploaded-image-preview');
            preview.src = e.target.result;
            preview.style.maxWidth = '300px';
            document.getElementById('upload-preview').style.display = 'block';
        };
        
        reader.readAsDataURL(file);
    }
}

async function processUploadedImage() {
    const input = document.getElementById('qr-image-upload');
    if (!input.files || !input.files[0]) {
        showMessage('error', 'Error', 'No image selected');
        return;
    }
    
    await processImageFile(input.files[0], 'upload');
}

async function validateManualQR() {
    const qrText = document.getElementById('qr-manual-input').value.trim();
    
    if (!qrText) {
        showMessage('error', 'Error', 'Please enter QR code text');
        return;
    }
    
    await validateQRData(qrText, 'manual');
}

async function processImageFile(file, source) {
    showModal('loading-modal', true);
    document.getElementById('loading-text').textContent = 'Processing image...';
    
    const formData = new FormData();
    formData.append('file', file);
    
    try {
        const response = await fetch(`${API_BASE_URL}/scan_uploaded_image`, {
            method: 'POST',
            body: formData
        });
        
        const data = await response.json();
        showModal('loading-modal', false);
        
        if (data.success) {
            await handleQRScanResult(data, source);
        } else {
            showMessage('error', 'Scan Failed', data.message || 'Failed to scan QR code');
        }
    } catch (error) {
        showModal('loading-modal', false);
        showMessage('error', 'Processing Error', 'Failed to process image');
    }
}

async function processImageBlob(blob, source) {
    showModal('loading-modal', true);
    document.getElementById('loading-text').textContent = 'Processing captured image...';
    
    const file = new File([blob], `capture_${Date.now()}.jpg`, { type: 'image/jpeg' });
    await processImageFile(file, source);
}

async function validateQRData(qrData, source) {
    showModal('loading-modal', true);
    document.getElementById('loading-text').textContent = 'Validating QR data...';
    
    try {
        const { response, data } = await apiCall('/validate_aadhaar', {
            method: 'POST',
            body: JSON.stringify({ qr_data: qrData })
        });
        
        showModal('loading-modal', false);
        await handleQRScanResult(data, source);
    } catch (error) {
        showModal('loading-modal', false);
        showMessage('error', 'Validation Error', 'Failed to validate QR code');
    }
}

async function handleQRScanResult(data, source) {
    const resultDiv = document.getElementById('qr-validation-result');
    const registerBtn = document.getElementById('register-submit');
    
    if (data.success) {
        currentQRData = data.qr_data || document.getElementById('qr-manual-input').value;
        currentAadhaarNumber = data.aadhaar_number;
        
        if (data.is_duplicate) {
            resultDiv.innerHTML = `
                <div class="validation-error">
                    <i class="fas fa-exclamation-triangle"></i> <strong>Aadhaar Already Registered!</strong>
                    <p>This Aadhaar number (${data.aadhaar_number}) is registered to: <strong>${data.existing_user}</strong></p>
                    <p>Please use a different Aadhaar card.</p>
                </div>
            `;
            isQRValidated = false;
            registerBtn.disabled = true;
        } else {
            resultDiv.innerHTML = `
                <div class="validation-success">
                    <i class="fas fa-check-circle"></i> <strong>Aadhaar Validated Successfully!</strong>
                    <p>Scanned via: ${source}</p>
                    <p>Aadhaar number: <strong>${data.aadhaar_number}</strong></p>
                </div>
            `;
            
            displayQRPreview(data.data);
            isQRValidated = true;
            registerBtn.disabled = false;
        }
    } else {
        resultDiv.innerHTML = `
            <div class="validation-error">
                <i class="fas fa-times-circle"></i> <strong>Validation Failed</strong>
                <p>${data.message}</p>
            </div>
        `;
        isQRValidated = false;
        registerBtn.disabled = true;
    }
}

function displayQRPreview(data) {
    const previewDiv = document.getElementById('qr-data-preview');
    const container = document.getElementById('qr-preview-register');
    
    if (data) {
        previewDiv.innerHTML = `
            <div class="qr-preview-card">
                ${data.name ? `<p><i class="fas fa-user"></i> <strong>Name:</strong> ${data.name}</p>` : ''}
                ${data.uid ? `<p><i class="fas fa-id-card"></i> <strong>Aadhaar:</strong> ${data.uid}</p>` : ''}
                ${data.dob ? `<p><i class="fas fa-birthday-cake"></i> <strong>DOB:</strong> ${data.dob}</p>` : ''}
                ${data.gender ? `<p><i class="fas fa-venus-mars"></i> <strong>Gender:</strong> ${data.gender}</p>` : ''}
                ${data.email ? `<p><i class="fas fa-envelope"></i> <strong>Email:</strong> ${data.email}</p>` : ''}
            </div>
        `;
        container.style.display = 'block';
    }
}

// Registration Form Submission
document.getElementById('register-form').addEventListener('submit', async function(e) {
    e.preventDefault();
    
    const username = document.getElementById('reg-username').value.trim();
    const password = document.getElementById('reg-password').value.trim();
    const fullName = document.getElementById('full-name').value.trim() || username;
    const qrData = currentQRData;
    const terms = document.getElementById('terms').checked;
    
    if (!username || !password) {
        showMessage('error', 'Error', 'Enter username and password');
        return;
    }
    
    if (!qrData) {
        showMessage('error', 'Error', 'Please scan your Aadhaar QR code');
        return;
    }
    
    if (!terms) {
        showMessage('error', 'Error', 'Please confirm Aadhaar information');
        return;
    }
    
    if (!isQRValidated) {
        showMessage('error', 'Error', 'Please validate your Aadhaar QR code');
        return;
    }
    
    if (username.length < 3) {
        showMessage('error', 'Error', 'Username must be at least 3 characters');
        return;
    }
    
    if (password.length < 6) {
        showMessage('error', 'Error', 'Password must be at least 6 characters');
        return;
    }
    
    showModal('loading-modal', true);
    document.getElementById('loading-text').textContent = 'Creating account...';
    
    try {
        const { response, data } = await apiCall('/register', {
            method: 'POST',
            body: JSON.stringify({ 
                username, 
                password, 
                fullName, 
                qr_data: qrData 
            })
        });
        
        showModal('loading-modal', false);
        
        if (data.success) {
            showMessage('success', 'Success', `Account created! Aadhaar ${data.user.aadhaar_number} verified.`);
            resetRegistrationForm();
            showPage('login-page');
        } else {
            showMessage('error', 'Registration Failed', data.message || 'Registration failed');
        }
    } catch (error) {
        showModal('loading-modal', false);
        showMessage('error', 'Connection Error', 'Failed to connect to server');
    }
});

function resetRegistrationForm() {
    document.getElementById('register-form').reset();
    document.getElementById('qr-manual-input').value = '';
    document.getElementById('qr-validation-result').innerHTML = '';
    document.getElementById('qr-preview-register').style.display = 'none';
    document.getElementById('qr-data-preview').innerHTML = '';
    document.getElementById('upload-preview').style.display = 'none';
    document.getElementById('webcam-container').style.display = 'none';
    stopWebcamScan();
    currentQRData = '';
    currentAadhaarNumber = '';
    isQRValidated = false;
    document.getElementById('register-submit').disabled = true;
}

// Dashboard Functions
function updateDashboardInfo(user) {
    document.getElementById('welcome-message').textContent = `Welcome, ${user.full_name}!`;
    document.getElementById('display-username').textContent = user.username;
    
    const badge = document.getElementById('verification-badge');
    if (user.is_verified && user.aadhaar_number) {
        badge.innerHTML = `<span class="verified-badge"><i class="fas fa-check-circle"></i> Aadhaar Verified</span>`;
        document.getElementById('aadhaar-status').innerHTML = '<span style="color: #28a745;">Verified</span>';
        document.getElementById('display-aadhaar').textContent = user.aadhaar_number;
    } else {
        badge.innerHTML = `<span class="unverified-badge"><i class="fas fa-exclamation-triangle"></i> Aadhaar Not Verified</span>`;
        document.getElementById('aadhaar-status').innerHTML = '<span style="color: #dc3545;">Not Verified</span>';
        document.getElementById('display-aadhaar').textContent = 'Not linked';
    }
}

// Dashboard Webcam Functions
async function dashboardWebcamScan() {
    showModal('webcam-modal', true);
}

let modalWebcamStream = null;

async function startModalWebcam() {
    try {
        const video = document.getElementById('modal-webcam-video');
        const constraints = {
            video: {
                width: { ideal: 1280 },
                height: { ideal: 720 },
                facingMode: 'environment'
            }
        };
        
        modalWebcamStream = await navigator.mediaDevices.getUserMedia(constraints);
        video.srcObject = modalWebcamStream;
        
        // Start automatic scanning
        startAutoScan();
    } catch (error) {
        console.error('Modal webcam error:', error);
        showMessage('error', 'Camera Error', 'Cannot access webcam');
    }
}

function stopModalWebcam() {
    if (modalWebcamStream) {
        modalWebcamStream.getTracks().forEach(track => track.stop());
        modalWebcamStream = null;
    }
    
    const video = document.getElementById('modal-webcam-video');
    if (video) {
        video.srcObject = null;
    }
    
    // Stop auto scanning
    if (scanInterval) {
        clearInterval(scanInterval);
        scanInterval = null;
    }
}

function startAutoScan() {
    scanInterval = setInterval(async () => {
        await captureFromModal(false); // Auto-scan
    }, 1000); // Scan every second
}

async function captureFromModal(manual = true) {
    const video = document.getElementById('modal-webcam-video');
    const canvas = document.createElement('canvas');
    
    if (!video || video.readyState !== 4) return;
    
    canvas.width = video.videoWidth;
    canvas.height = video.videoHeight;
    const ctx = canvas.getContext('2d');
    ctx.drawImage(video, 0, 0, canvas.width, canvas.height);
    
    canvas.toBlob(async (blob) => {
        const file = new File([blob], `scan_${Date.now()}.jpg`, { type: 'image/jpeg' });
        await processDashboardImage(file, 'webcam');
    }, 'image/jpeg', 0.8);
}

async function dashboardUploadQR() {
    const input = document.getElementById('dashboard-qr-file');
    if (!input.files || !input.files[0]) {
        showMessage('error', 'Error', 'Select an image file');
        return;
    }
    
    const file = input.files[0];
    
    // Show preview
    const reader = new FileReader();
    reader.onload = function(e) {
        const preview = document.getElementById('dashboard-uploaded-image');
        preview.src = e.target.result;
        preview.style.maxWidth = '300px';
        document.getElementById('dashboard-upload-preview').style.display = 'block';
    };
    reader.readAsDataURL(file);
    
    await processDashboardImage(file, 'upload');
}

async function dashboardUploadQRText() {
    const qrText = document.getElementById('dashboard-qr-text').value.trim();
    
    if (!qrText) {
        showMessage('error', 'Error', 'Enter QR code text');
        return;
    }
    
    await processDashboardQRText(qrText);
}

async function processDashboardImage(file, source) {
    showModal('loading-modal', true);
    document.getElementById('loading-text').textContent = 'Scanning QR code...';
    
    const username = localStorage.getItem('username');
    const formData = new FormData();
    formData.append('qr_image', file);
    formData.append('username', username);
    
    try {
        const response = await fetch(`${API_BASE_URL}/upload_qr`, {
            method: 'POST',
            body: formData
        });
        
        const data = await response.json();
        showModal('loading-modal', false);
        
        if (data.success) {
            document.getElementById('qr-result').innerHTML = `
                <div class="success-message">
                    <i class="fas fa-check-circle"></i> <strong>QR Updated Successfully!</strong>
                    <p>Scanned via: ${source}</p>
                    ${data.data && data.data.name ? `<p><strong>Name:</strong> ${data.data.name}</p>` : ''}
                    ${data.data && data.data.uid ? `<p><strong>Aadhaar:</strong> ${data.data.uid}</p>` : ''}
                </div>
            `;
            
            // Update user info
            if (data.data && data.data.uid) {
                localStorage.setItem('aadhaar_number', data.data.uid);
                localStorage.setItem('is_verified', 'true');
                updateDashboardInfo({
                    username: localStorage.getItem('username'),
                    full_name: localStorage.getItem('full_name'),
                    aadhaar_number: data.data.uid,
                    is_verified: true
                });
            }
            
            displayQRData(data.data);
            
            // If scanning from modal, close it on success
            if (source === 'webcam' && data.success) {
                setTimeout(() => {
                    closeWebcamModal();
                    showMessage('success', 'Success', 'Aadhaar QR scanned and updated');
                }, 1000);
            }
        } else {
            showMessage('error', 'Scan Failed', data.message || 'Failed to scan QR code');
        }
    } catch (error) {
        showModal('loading-modal', false);
        showMessage('error', 'Processing Error', 'Failed to process image');
    }
}

async function processDashboardQRText(qrText) {
    showModal('loading-modal', true);
    document.getElementById('loading-text').textContent = 'Processing QR text...';
    
    const username = localStorage.getItem('username');
    
    try {
        const { response, data } = await apiCall('/upload_qr_text', {
            method: 'POST',
            body: JSON.stringify({ username, qr_text: qrText })
        });
        
        showModal('loading-modal', false);
        
        if (data.success) {
            document.getElementById('qr-result').innerHTML = `
                <div class="success-message">
                    <i class="fas fa-check-circle"></i> <strong>QR Text Processed Successfully!</strong>
                    ${data.data && data.data.name ? `<p><strong>Name:</strong> ${data.data.name}</p>` : ''}
                    ${data.data && data.data.uid ? `<p><strong>Aadhaar:</strong> ${data.data.uid}</p>` : ''}
                </div>
            `;
            
            // Update user info
            if (data.data && data.data.uid) {
                localStorage.setItem('aadhaar_number', data.data.uid);
                localStorage.setItem('is_verified', 'true');
                updateDashboardInfo({
                    username: localStorage.getItem('username'),
                    full_name: localStorage.getItem('full_name'),
                    aadhaar_number: data.data.uid,
                    is_verified: true
                });
            }
            
            displayQRData(data.data);
            showMessage('success', 'Success', 'QR text processed');
        } else {
            showMessage('error', 'Processing Failed', data.message || 'Failed to process QR text');
        }
    } catch (error) {
        showModal('loading-modal', false);
        showMessage('error', 'Error', 'Failed to process QR text');
    }
}

function displayQRData(data) {
    const qrDataDiv = document.getElementById('qr-data');
    const infoDisplay = document.getElementById('qr-info-display');
    
    if (!data || Object.keys(data).length === 0) {
        qrDataDiv.innerHTML = `
            <div class="no-data-message">
                <p><i class="fas fa-exclamation-triangle"></i> No valid Aadhaar data</p>
            </div>
        `;
    } else {
        qrDataDiv.innerHTML = `
            <div class="qr-data-card">
                ${data.name ? `<p><i class="fas fa-user"></i> <strong>Name:</strong> ${data.name}</p>` : ''}
                ${data.uid ? `<p><i class="fas fa-id-card"></i> <strong>Aadhaar:</strong> ${data.uid}</p>` : ''}
                ${data.dob ? `<p><i class="fas fa-birthday-cake"></i> <strong>DOB:</strong> ${data.dob}</p>` : ''}
                ${data.gender ? `<p><i class="fas fa-venus-mars"></i> <strong>Gender:</strong> ${data.gender}</p>` : ''}
                ${data.email ? `<p><i class="fas fa-envelope"></i> <strong>Email:</strong> ${data.email}</p>` : ''}
                ${data.mobile ? `<p><i class="fas fa-phone"></i> <strong>Mobile:</strong> ${data.mobile}</p>` : ''}
                ${data.address ? `<p><i class="fas fa-home"></i> <strong>Address:</strong> ${data.address}</p>` : ''}
            </div>
        `;
    }
    
    infoDisplay.style.display = 'block';
}

function logout() {
    // Stop any active webcam streams
    if (webcamStream) {
        webcamStream.getTracks().forEach(track => track.stop());
    }
    if (modalWebcamStream) {
        modalWebcamStream.getTracks().forEach(track => track.stop());
    }
    
    // Clear intervals
    if (scanInterval) {
        clearInterval(scanInterval);
    }
    
    // Clear localStorage
    localStorage.removeItem('username');
    localStorage.removeItem('full_name');
    localStorage.removeItem('aadhaar_number');
    localStorage.removeItem('is_verified');
    
    // Reset forms
    document.getElementById('login-form').reset();
    resetRegistrationForm();
    document.getElementById('dashboard-qr-file').value = '';
    document.getElementById('dashboard-qr-text').value = '';
    document.getElementById('qr-result').innerHTML = '';
    document.getElementById('qr-data').innerHTML = '';
    document.getElementById('qr-info-display').style.display = 'none';
    document.getElementById('dashboard-upload-preview').style.display = 'none';
    
    showPage('login-page');
    showMessage('success', 'Logged Out', 'Logged out successfully');
}

// Initialize
document.addEventListener('DOMContentLoaded', async function() {
    // Check server connection
    try {
        const { data } = await apiCall('/health');
        console.log('Server connected:', data.status);
    } catch (error) {
        setTimeout(() => {
            showMessage('error', 'Server Connection', 'Cannot connect to server. Make sure backend is running.');
        }, 1000);
    }
    
    // Setup modal close buttons
    const closeBtn = document.getElementById('message-close');
    if (closeBtn) {
        closeBtn.addEventListener('click', function() {
            showModal('message-modal', false);
        });
    }
    
    // Close modals when clicking outside
    document.querySelectorAll('.modal').forEach(modal => {
        modal.addEventListener('click', function(e) {
            if (e.target === modal) {
                if (modal.id === 'webcam-modal') {
                    closeWebcamModal();
                } else {
                    showModal(modal.id, false);
                }
            }
        });
    });
    
    // Check if user is already logged in
    const username = localStorage.getItem('username');
    if (username) {
        const fullName = localStorage.getItem('full_name') || username;
        const aadhaarNumber = localStorage.getItem('aadhaar_number');
        const isVerified = localStorage.getItem('is_verified') === 'true';
        
        updateDashboardInfo({
            username: username,
            full_name: fullName,
            aadhaar_number: aadhaarNumber,
            is_verified: isVerified
        });
        
        showPage('dashboard-page');
    }
    
    // Setup file input change handlers
    const qrImageUpload = document.getElementById('qr-image-upload');
    if (qrImageUpload) {
        qrImageUpload.addEventListener('change', function() {
            handleImageUpload(this);
        });
    }
    
    const dashboardQrFile = document.getElementById('dashboard-qr-file');
    if (dashboardQrFile) {
        dashboardQrFile.addEventListener('change', function() {
            if (this.files && this.files[0]) {
                const reader = new FileReader();
                reader.onload = function(e) {
                    const preview = document.getElementById('dashboard-uploaded-image');
                    preview.src = e.target.result;
                    preview.style.maxWidth = '300px';
                    document.getElementById('dashboard-upload-preview').style.display = 'block';
                };
                reader.readAsDataURL(this.files[0]);
            }
        });
    }
});