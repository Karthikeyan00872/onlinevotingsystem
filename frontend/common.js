// Common.js - Shared JavaScript for Online Voting System
const API_BASE_URL = '/api';  // Relative path for better compatibility

// Common utility functions
function showModal(modalId, show = true) {
    const modal = document.getElementById(modalId);
    if (modal) {
        modal.style.display = show ? 'flex' : 'none';
        
        // Prevent scrolling when modal is open
        if (show) {
            document.body.style.overflow = 'hidden';
        } else {
            document.body.style.overflow = 'auto';
        }
    }
}

function showMessage(type, title, message) {
    const icon = document.getElementById('message-icon');
    const titleEl = document.getElementById('message-title');
    const contentEl = document.getElementById('message-content');
    
    if (!icon || !titleEl || !contentEl) {
        console.error('Message modal elements not found');
        alert(`${title}: ${message}`);
        return;
    }
    
    // Set icon based on message type
    switch(type) {
        case 'success':
            icon.innerHTML = '<i class="fas fa-check-circle"></i>';
            icon.style.color = '#28a745';
            break;
        case 'error':
            icon.innerHTML = '<i class="fas fa-times-circle"></i>';
            icon.style.color = '#dc3545';
            break;
        case 'warning':
            icon.innerHTML = '<i class="fas fa-exclamation-triangle"></i>';
            icon.style.color = '#ffc107';
            break;
        case 'info':
        default:
            icon.innerHTML = '<i class="fas fa-info-circle"></i>';
            icon.style.color = '#17a2b8';
            break;
    }
    
    icon.style.fontSize = '50px';
    titleEl.textContent = title;
    contentEl.textContent = message;
    
    // Set close button handler
    const closeBtn = document.getElementById('message-close');
    if (closeBtn) {
        closeBtn.onclick = () => showModal('message-modal', false);
    }
    
    showModal('message-modal', true);
}

function togglePassword(inputId) {
    const input = document.getElementById(inputId);
    if (!input) {
        console.error(`Input element with id ${inputId} not found`);
        return;
    }
    
    const button = input.nextElementSibling;
    if (!button || !button.classList.contains('btn-toggle-password')) {
        console.error('Toggle password button not found');
        return;
    }
    
    const icon = button.querySelector('i');
    if (!icon) {
        console.error('Icon element not found in toggle button');
        return;
    }
    
    if (input.type === 'password') {
        input.type = 'text';
        icon.classList.remove('fa-eye');
        icon.classList.add('fa-eye-slash');
        button.setAttribute('aria-label', 'Hide password');
    } else {
        input.type = 'password';
        icon.classList.remove('fa-eye-slash');
        icon.classList.add('fa-eye');
        button.setAttribute('aria-label', 'Show password');
    }
}

// Page navigation for single-page applications
function showPage(pageId) {
    const pages = document.querySelectorAll('.page');
    if (pages.length === 0) {
        console.error('No page elements found');
        return;
    }
    
    pages.forEach(page => {
        page.classList.remove('active');
    });
    
    const targetPage = document.getElementById(pageId);
    if (targetPage) {
        targetPage.classList.add('active');
        
        // Scroll to top when switching pages
        window.scrollTo({ top: 0, behavior: 'smooth' });
        
        // Trigger page-specific initialization if function exists
        if (typeof window[`init${pageId.replace(/-/g, '')}`] === 'function') {
            window[`init${pageId.replace(/-/g, '')}`]();
        }
    } else {
        console.error(`Page with id ${pageId} not found`);
    }
}

// API call wrapper with improved error handling
async function apiCall(endpoint, options = {}) {
    const url = `${API_BASE_URL}${endpoint}`;
    
    const defaultOptions = {
        headers: {
            'Accept': 'application/json',
            'X-Requested-With': 'XMLHttpRequest'
        },
        ...options
    };
    
    // Set Content-Type for JSON requests
    if (options.body && typeof options.body === 'string') {
        defaultOptions.headers['Content-Type'] = 'application/json';
    }
    
    // Add authentication token if available
    const adminToken = localStorage.getItem('admin_token');
    if (adminToken && endpoint.startsWith('/admin')) {
        defaultOptions.headers['Authorization'] = `Bearer ${adminToken}`;
    }
    
    try {
        console.log(`API Call: ${url}`, options.method || 'GET');
        
        const response = await fetch(url, defaultOptions);
        const contentType = response.headers.get('content-type');
        
        let data;
        
        if (contentType && contentType.includes('application/json')) {
            data = await response.json();
        } else {
            const text = await response.text();
            console.warn('Non-JSON response received:', text.substring(0, 100));
            data = { message: text, success: false };
        }
        
        console.log(`API Response (${response.status}):`, data);
        
        return {
            response,
            data,
            ok: response.ok,
            status: response.status
        };
    } catch (error) {
        console.error('API Network Error:', error);
        
        // Show user-friendly error message
        let errorMessage = 'Failed to connect to server. ';
        if (navigator.onLine) {
            errorMessage += 'Please check if the server is running.';
        } else {
            errorMessage += 'Please check your internet connection.';
        }
        
        // Don't show modal if we're already showing one
        if (!document.getElementById('loading-modal').style.display === 'flex') {
            showMessage('error', 'Connection Error', errorMessage);
        }
        
        throw error;
    }
}

// Form validation utilities
function validateEmail(email) {
    const re = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
    return re.test(email);
}

function validateAadhaar(aadhaar) {
    const re = /^\d{4}\s?\d{4}\s?\d{4}$/;
    return re.test(aadhaar);
}

function validatePassword(password) {
    return password.length >= 6;
}

function validateUsername(username) {
    return username.length >= 3 && /^[a-zA-Z0-9_]+$/.test(username);
}

// Local storage utilities
function getCurrentUser() {
    const username = localStorage.getItem('username');
    const fullName = localStorage.getItem('full_name');
    const aadhaarNumber = localStorage.getItem('aadhaar_number');
    const isVerified = localStorage.getItem('is_verified') === 'true';
    
    if (!username) return null;
    
    return {
        username,
        full_name: fullName || username,
        aadhaar_number: aadhaarNumber,
        is_verified: isVerified
    };
}

function getCurrentAdmin() {
    const adminUsername = localStorage.getItem('admin_username');
    const adminToken = localStorage.getItem('admin_token');
    
    if (!adminUsername || !adminToken) return null;
    
    return {
        username: adminUsername,
        token: adminToken
    };
}

function clearAuth() {
    localStorage.removeItem('username');
    localStorage.removeItem('full_name');
    localStorage.removeItem('aadhaar_number');
    localStorage.removeItem('is_verified');
    localStorage.removeItem('admin_username');
    localStorage.removeItem('admin_token');
}

// Formatting utilities
function formatDate(dateString) {
    if (!dateString) return 'N/A';
    
    try {
        const date = new Date(dateString);
        return date.toLocaleDateString('en-US', {
            year: 'numeric',
            month: 'short',
            day: 'numeric',
            hour: '2-digit',
            minute: '2-digit'
        });
    } catch (error) {
        return dateString;
    }
}

function formatNumber(num) {
    if (num === undefined || num === null) return '0';
    return num.toString().replace(/\B(?=(\d{3})+(?!\d))/g, ",");
}

function formatPercentage(value, total) {
    if (!total || total === 0) return '0%';
    const percentage = (value / total * 100).toFixed(1);
    return `${percentage}%`;
}

// Loading state management
let loadingCount = 0;

function showLoading(message = 'Loading...') {
    loadingCount++;
    const loadingText = document.getElementById('loading-text');
    if (loadingText) {
        loadingText.textContent = message;
    }
    showModal('loading-modal', true);
}

function hideLoading() {
    loadingCount = Math.max(0, loadingCount - 1);
    if (loadingCount === 0) {
        showModal('loading-modal', false);
    }
}

// Debounce function for search/input
function debounce(func, wait) {
    let timeout;
    return function executedFunction(...args) {
        const later = () => {
            clearTimeout(timeout);
            func(...args);
        };
        clearTimeout(timeout);
        timeout = setTimeout(later, wait);
    };
}

// Create loading spinner dynamically
function createSpinner(size = 'medium') {
    const spinner = document.createElement('div');
    spinner.className = 'spinner';
    
    const sizes = {
        'small': '20px',
        'medium': '40px',
        'large': '60px'
    };
    
    spinner.style.width = sizes[size] || sizes.medium;
    spinner.style.height = sizes[size] || sizes.medium;
    spinner.style.border = '3px solid #f3f3f3';
    spinner.style.borderTop = '3px solid #667eea';
    spinner.style.borderRadius = '50%';
    spinner.style.animation = 'spin 1s linear infinite';
    
    return spinner;
}

// Add CSS for spinner animation if not already present
if (!document.getElementById('common-styles')) {
    const style = document.createElement('style');
    style.id = 'common-styles';
    style.textContent = `
        @keyframes spin {
            0% { transform: rotate(0deg); }
            100% { transform: rotate(360deg); }
        }
        
        .spinner {
            animation: spin 1s linear infinite;
        }
        
        .btn:disabled {
            opacity: 0.6;
            cursor: not-allowed;
        }
        
        .required::after {
            content: " *";
            color: #dc3545;
        }
        
        .validation-success {
            color: #28a745;
            padding: 10px;
            background: #f8fff8;
            border-radius: 5px;
            border: 1px solid #28a745;
            margin: 10px 0;
        }
        
        .validation-error {
            color: #dc3545;
            padding: 10px;
            background: #fff8f8;
            border-radius: 5px;
            border: 1px solid #dc3545;
            margin: 10px 0;
        }
    `;
    document.head.appendChild(style);
}

// Initialize common functionality
document.addEventListener('DOMContentLoaded', function() {
    console.log('Common.js initialized');
    
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
                showModal(modal.id, false);
            }
        });
    });
    
    // Close modals with Escape key
    document.addEventListener('keydown', function(e) {
        if (e.key === 'Escape') {
            document.querySelectorAll('.modal').forEach(modal => {
                if (modal.style.display === 'flex') {
                    showModal(modal.id, false);
                }
            });
        }
    });
    
    // Auto-hide success messages after 5 seconds
    setInterval(() => {
        const successMessages = document.querySelectorAll('.success-message');
        successMessages.forEach(msg => {
            if (msg.style.display !== 'none') {
                const created = parseInt(msg.getAttribute('data-created') || '0');
                if (created && Date.now() - created > 5000) {
                    msg.style.display = 'none';
                }
            }
        });
    }, 1000);
    
    // Add spinner animation to existing spinners
    document.querySelectorAll('.spinner').forEach(spinner => {
        if (!spinner.style.animation) {
            spinner.style.animation = 'spin 1s linear infinite';
        }
    });
    
    // Initialize tooltips for elements with title attribute
    document.querySelectorAll('[title]').forEach(element => {
        element.addEventListener('mouseenter', function(e) {
            const title = this.getAttribute('title');
            if (title) {
                const tooltip = document.createElement('div');
                tooltip.className = 'tooltip';
                tooltip.textContent = title;
                tooltip.style.position = 'absolute';
                tooltip.style.background = '#333';
                tooltip.style.color = 'white';
                tooltip.style.padding = '5px 10px';
                tooltip.style.borderRadius = '4px';
                tooltip.style.fontSize = '12px';
                tooltip.style.zIndex = '1000';
                tooltip.style.whiteSpace = 'nowrap';
                
                document.body.appendChild(tooltip);
                
                const rect = this.getBoundingClientRect();
                tooltip.style.top = (rect.top - tooltip.offsetHeight - 5) + 'px';
                tooltip.style.left = (rect.left + rect.width / 2 - tooltip.offsetWidth / 2) + 'px';
                
                this.setAttribute('data-tooltip-id', tooltip.id);
                
                this.addEventListener('mouseleave', function() {
                    if (tooltip && tooltip.parentNode) {
                        tooltip.parentNode.removeChild(tooltip);
                    }
                }, { once: true });
            }
        });
    });
    
    // Test API connection on load
    setTimeout(async () => {
        try {
            const { data } = await apiCall('/test');
            console.log('Server status:', data);
            
            if (data.mongodb === false) {
                console.warn('MongoDB is not connected. Using mock data.');
            }
            
            if (data.qr_scanning === 'Unavailable') {
                console.warn('QR scanning is not available. Install opencv-python and pyzbar for full functionality.');
            }
        } catch (error) {
            console.warn('Could not reach server:', error.message);
        }
    }, 1000);
    
    // Handle back button for SPA
    window.addEventListener('popstate', function(event) {
        const currentPage = window.location.hash.replace('#', '');
        if (currentPage) {
            showPage(currentPage);
        }
    });
    
    // Initialize current page from URL hash
    const hash = window.location.hash.replace('#', '');
    if (hash) {
        setTimeout(() => showPage(hash), 100);
    }
});

// Export functions for use in other scripts
if (typeof window !== 'undefined') {
    window.common = {
        showModal,
        showMessage,
        togglePassword,
        showPage,
        apiCall,
        validateEmail,
        validateAadhaar,
        validatePassword,
        validateUsername,
        getCurrentUser,
        getCurrentAdmin,
        clearAuth,
        formatDate,
        formatNumber,
        formatPercentage,
        showLoading,
        hideLoading,
        debounce,
        createSpinner
    };
}

// Debug helper
function debugLog(component, message, data = null) {
    if (window.location.hostname === 'localhost' || window.location.hostname === '127.0.0.1') {
        console.log(`[${component}] ${message}`, data || '');
    }
}

// Error boundary for async functions
async function safeApiCall(endpoint, options = {}) {
    try {
        return await apiCall(endpoint, options);
    } catch (error) {
        console.error('Safe API call failed:', error);
        return {
            response: { ok: false, status: 0 },
            data: { success: false, message: 'Network error occurred' },
            ok: false,
            status: 0
        };
    }
}

// Session timeout detection
let lastActivity = Date.now();
const SESSION_TIMEOUT = 30 * 60 * 1000; // 30 minutes

function updateActivity() {
    lastActivity = Date.now();
}

document.addEventListener('mousemove', updateActivity);
document.addEventListener('keypress', updateActivity);
document.addEventListener('click', updateActivity);

setInterval(() => {
    const user = getCurrentUser();
    const admin = getCurrentAdmin();
    
    if ((user || admin) && Date.now() - lastActivity > SESSION_TIMEOUT) {
        showMessage('warning', 'Session Timeout', 'Your session has expired due to inactivity.');
        clearAuth();
        
        if (window.location.pathname.includes('admin')) {
            window.location.href = '/admin';
        } else {
            window.location.href = '/';
        }
    }
}, 60000); // Check every minute