# app.py
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_from_directory
from flask_cors import CORS
from pymongo import MongoClient
from bson import ObjectId
import os
import json
import hashlib
import base64
import xml.etree.ElementTree as ET
import re
import zlib
from datetime import datetime, timedelta
from functools import wraps
import uuid
from werkzeug.utils import secure_filename

# Try to import QR scanning libraries
try:
    import cv2
    import numpy as np
    from pyzbar.pyzbar import decode
    QR_SCANNING_AVAILABLE = True
except ImportError:
    QR_SCANNING_AVAILABLE = False
    print("Warning: QR scanning libraries not available. Install with: pip install opencv-python pyzbar")

# Import AadhaarQRDecoder from aadhar1.py concept
class AadhaarQRDecoder:
    """Class to decode Aadhaar QR code data"""
    
    def __init__(self, qr_data: str):
        self.qr_data = qr_data.strip()
        self.decoded_data = {}
        
    def decode(self) -> dict:
        """Decode QR data based on format"""
        if self._is_xml_format():
            return self._parse_xml_format()
        elif self._is_compressed_format():
            return self._parse_compressed_format()
        elif self._is_plain_text_format():
            return self._parse_plain_text_format()
        else:
            raise ValueError("Unsupported QR code format")
    
    def _is_xml_format(self) -> bool:
        return (self.qr_data.startswith('<?xml') or 
                self.qr_data.startswith('<PrintLetterBarcodeData'))
    
    def _is_compressed_format(self) -> bool:
        return self.qr_data.startswith('V')
    
    def _is_plain_text_format(self) -> bool:
        return '|' in self.qr_data and not self.qr_data.startswith('<?xml')
    
    def _parse_xml_format(self) -> dict:
        try:
            xml_data = self.qr_data
            if not xml_data.startswith('<?xml'):
                xml_data = '<?xml version="1.0" encoding="UTF-8"?>' + xml_data
            
            root = ET.fromstring(xml_data)
            data = {}
            for attr_name, attr_value in root.attrib.items():
                data[attr_name.lower()] = attr_value
            
            return data
        except ET.ParseError as e:
            raise ValueError(f"Failed to parse XML: {e}")
    
    def _parse_compressed_format(self) -> dict:
        try:
            compressed_data = base64.b64decode(self.qr_data[1:])
            xml_data = zlib.decompress(compressed_data, 15 + 32).decode('utf-8')
            self.qr_data = xml_data
            return self._parse_xml_format()
        except Exception as e:
            raise ValueError(f"Failed to decode compressed data: {e}")
    
    def _parse_plain_text_format(self) -> dict:
        try:
            parts = self.qr_data.split('|')
            data = {
                'uid': parts[0] if len(parts) > 0 else '',
                'name': parts[1] if len(parts) > 1 else '',
                'dob': parts[2] if len(parts) > 2 else '',
                'gender': parts[3] if len(parts) > 3 else '',
                'co': parts[4] if len(parts) > 4 else '',
                'house': parts[5] if len(parts) > 5 else '',
                'street': parts[6] if len(parts) > 6 else '',
                'landmark': parts[7] if len(parts) > 7 else '',
                'locality': parts[8] if len(parts) > 8 else '',
                'vtc': parts[9] if len(parts) > 9 else '',
                'dist': parts[10] if len(parts) > 10 else '',
                'state': parts[11] if len(parts) > 11 else '',
                'pc': parts[12] if len(parts) > 12 else '',
                'email': parts[13] if len(parts) > 13 else '',
                'mobile': parts[14] if len(parts) > 14 else '',
            }
            return {k: v for k, v in data.items() if v}
        except Exception as e:
            raise ValueError(f"Failed to parse plain text format: {e}")

# Initialize Flask app
app = Flask(__name__, static_folder='.', static_url_path='')
app.secret_key = 'your-secret-key-here-change-in-production'
CORS(app)

# Configuration for file uploads
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5MB max file size
app.config['UPLOAD_FOLDER'] = 'static/candidate_images'
app.config['ALLOWED_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif'}

def allowed_file(filename):
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

# MongoDB connection
try:
    # Connect to MongoDB (default: localhost:27017)
    client = MongoClient('mongodb://localhost:27017/', serverSelectionTimeoutMS=5000)
    db = client['online_voting']
    
    # Collections
    users_collection = db['users']
    admins_collection = db['admins']
    candidates_collection = db['candidates']
    votes_collection = db['votes']
    
    # Test the connection
    client.server_info()
    mongodb_connected = True
    print("✅ Successfully connected to MongoDB")
except Exception as e:
    print(f"❌ Error connecting to MongoDB: {e}")
    print("⚠️ Using in-memory storage as fallback")
    users_collection = None
    admins_collection = None
    candidates_collection = None
    votes_collection = None
    mongodb_connected = False

# In-memory fallback storage (if MongoDB not available)
if not mongodb_connected:
    # Use file-backed JSON storage as a simple persistent fallback
    DATA_DIR = 'data'
    USERS_FILE = os.path.join(DATA_DIR, 'users.json')
    ADMINS_FILE = os.path.join(DATA_DIR, 'admins.json')
    CANDIDATES_FILE = os.path.join(DATA_DIR, 'candidates.json')
    VOTES_FILE = os.path.join(DATA_DIR, 'votes.json')

    if not os.path.exists(DATA_DIR):
        os.makedirs(DATA_DIR)

    def _json_serializer(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Type {type(obj)} not serializable")

    def _save_json(file_path, data):
        try:
            with open(file_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2, default=_json_serializer)
        except Exception as e:
            print(f"Warning: Failed to save {file_path}: {e}")

    def _load_json(file_path, default):
        try:
            if os.path.exists(file_path):
                with open(file_path, 'r', encoding='utf-8') as f:
                    return json.load(f)
        except Exception as e:
            print(f"Warning: Failed to load {file_path}: {e}")
        return default

    # Load users and admins
    default_users = {
        'admin': {
            'password': 'admin123',
            'role': 'admin',
            'full_name': 'System Administrator'
        }
    }
    users_db = _load_json(USERS_FILE, default_users)
    admins_db = _load_json(ADMINS_FILE, {})

    # Ensure test voter exists
    if not users_db.get('testvoter'):
        users_db['testvoter'] = {
            'password': 'test123',
            'role': 'voter',
            'full_name': 'Test Voter',
            'aadhaar_number': '123456789012',
            'is_verified': True,
            'has_voted': False
        }

    # Load candidates (keys stored as strings in JSON, convert to int)
    raw_candidates = _load_json(CANDIDATES_FILE, {})
    try:
        candidates_db = {int(k): v for k, v in raw_candidates.items()} if isinstance(raw_candidates, dict) else {}
    except Exception:
        candidates_db = {}

    if len(candidates_db) == 0:
        candidates_db[1] = {
            'candidate_id': 1,
            'name': 'John Doe',
            'party': 'Democratic Party',
            'is_active': True,
            'votes': 0
        }
        candidates_db[2] = {
            'candidate_id': 2,
            'name': 'Jane Smith',
            'party': 'Republican Party',
            'is_active': True,
            'votes': 0
        }

    votes_db = _load_json(VOTES_FILE, [])

    # Save helpers
    def save_users():
        _save_json(USERS_FILE, users_db)

    def save_admins():
        _save_json(ADMINS_FILE, admins_db)

    def save_candidates():
        serializable = {str(k): v for k, v in candidates_db.items()}
        _save_json(CANDIDATES_FILE, serializable)

    def save_votes():
        _save_json(VOTES_FILE, votes_db)

    # Persist defaults if any
    save_users()
    save_admins()
    save_candidates()
    save_votes()

# Helper functions for MongoDB/fallback
def get_user(username):
    """Get user from MongoDB or fallback"""
    if mongodb_connected and users_collection is not None:
        return users_collection.find_one({"username": username})
    elif not mongodb_connected:
        return users_db.get(username)
    return None

def get_admin(username):
    """Get admin from MongoDB or fallback"""
    if mongodb_connected and admins_collection is not None:
        return admins_collection.find_one({"username": username})
    elif not mongodb_connected:
        return admins_db.get(username)
    return None

def get_candidate(candidate_id):
    """Get candidate from MongoDB or fallback"""
    candidate_id = int(candidate_id) if isinstance(candidate_id, (str, float)) else candidate_id
    if mongodb_connected and candidates_collection is not None:
        return candidates_collection.find_one({"candidate_id": candidate_id})
    elif not mongodb_connected:
        return candidates_db.get(candidate_id)
    return None

def get_all_candidates():
    """Get all candidates from MongoDB or fallback"""
    if mongodb_connected and candidates_collection is not None:
        return list(candidates_collection.find({}))
    elif not mongodb_connected:
        return list(candidates_db.values())
    return []

def get_all_votes():
    """Get all votes from MongoDB or fallback"""
    if mongodb_connected and votes_collection is not None:
        return list(votes_collection.find({}))
    elif not mongodb_connected:
        return votes_db.copy()
    return []

def create_user(user_data):
    """Create new user in MongoDB or fallback"""
    if mongodb_connected and users_collection is not None:
        result = users_collection.insert_one(user_data)
        return result.inserted_id
    elif not mongodb_connected:
        username = user_data['username']
        users_db[username] = user_data
        try:
            save_users()
        except Exception:
            pass
        return username
    return None

def create_admin(admin_data):
    """Create new admin in MongoDB or fallback"""
    if mongodb_connected and admins_collection is not None:
        result = admins_collection.insert_one(admin_data)
        return result.inserted_id
    elif not mongodb_connected:
        username = admin_data['username']
        admins_db[username] = admin_data
        try:
            save_admins()
        except Exception:
            pass
        return username
    return None

def create_candidate(candidate_data):
    """Create new candidate in MongoDB or fallback"""
    if mongodb_connected and candidates_collection is not None:
        result = candidates_collection.insert_one(candidate_data)
        return result.inserted_id
    elif not mongodb_connected:
        candidate_id = candidate_data['candidate_id']
        candidates_db[candidate_id] = candidate_data
        try:
            save_candidates()
        except Exception:
            pass
        return candidate_id
    return None

def create_vote(vote_data):
    """Create new vote in MongoDB or fallback"""
    if mongodb_connected and votes_collection is not None:
        result = votes_collection.insert_one(vote_data)
        return result.inserted_id
    elif not mongodb_connected:
        votes_db.append(vote_data)
        try:
            save_votes()
        except Exception:
            pass
        return vote_data['vote_id']
    return None

def update_user(username, update_data):
    """Update user in MongoDB or fallback"""
    if mongodb_connected and users_collection is not None:
        users_collection.update_one({"username": username}, {"$set": update_data})
    elif not mongodb_connected and username in users_db:
        users_db[username].update(update_data)
        try:
            save_users()
        except Exception:
            pass

def update_candidate(candidate_id, update_data):
    """Update candidate in MongoDB or fallback"""
    candidate_id = int(candidate_id) if isinstance(candidate_id, (str, float)) else candidate_id
    if mongodb_connected and candidates_collection is not None:
        candidates_collection.update_one({"candidate_id": candidate_id}, {"$set": update_data})
    elif not mongodb_connected and candidate_id in candidates_db:
        candidates_db[candidate_id].update(update_data)
        try:
            save_candidates()
        except Exception:
            pass

def delete_candidate(candidate_id):
    """Delete candidate from MongoDB or fallback"""
    candidate_id = int(candidate_id) if isinstance(candidate_id, (str, float)) else candidate_id
    if mongodb_connected and candidates_collection is not None:
        result = candidates_collection.delete_one({"candidate_id": candidate_id})
        return result.deleted_count > 0
    elif not mongodb_connected and candidate_id in candidates_db:
        del candidates_db[candidate_id]
        try:
            save_candidates()
        except Exception:
            pass
        return True
    return False

def get_total_voters():
    """Get total number of voters"""
    if mongodb_connected and users_collection is not None:
        return users_collection.count_documents({"role": "voter"})
    elif not mongodb_connected:
        return sum(1 for user in users_db.values() if user.get('role') == 'voter')
    return 0

def get_total_votes():
    """Get total number of votes"""
    if mongodb_connected and votes_collection is not None:
        return votes_collection.count_documents({})
    elif not mongodb_connected:
        return len(votes_db)
    return 0

def get_total_candidates():
    """Get total number of active candidates"""
    if mongodb_connected and candidates_collection is not None:
        return candidates_collection.count_documents({"is_active": True})
    elif not mongodb_connected:
        return sum(1 for c in candidates_db.values() if c.get('is_active', True))
    return 0

# Initialize default admin if not exists
def init_default_admin():
    """Initialize default admin account"""
    print("🔍 Checking for admin account...")
    
    if mongodb_connected and admins_collection is not None:
        admin = admins_collection.find_one({"username": "admin"})
        if admin:
            print("✅ Admin account found in MongoDB")
            return True
        
        print("⚠️ Admin account not found, creating...")
        admin_data = {
            "username": "admin",
            "password": "admin123",
            "role": "admin",
            "full_name": "System Administrator",
            "created_at": datetime.now()
        }
        try:
            admins_collection.insert_one(admin_data)
            print("✅ Admin account created in MongoDB")
            return True
        except Exception as e:
            print(f"❌ Failed to create admin in MongoDB: {e}")
            return False
    elif not mongodb_connected:
        if "admin" in admins_db:
            print("✅ Admin account found in memory")
            return True
        else:
            print("⚠️ Admin account not found, creating in memory...")
            admins_db["admin"] = {
                "username": "admin",
                "password": "admin123",
                "role": "admin",
                "full_name": "System Administrator",
                "created_at": datetime.now()
            }
            print("✅ Admin account created in memory")
            return True
    
    return False

def init_default_data():
    """Initialize default test data"""
    print("🔍 Initializing default data...")
    
    # Remove all existing test data
    if mongodb_connected:
        # Remove test voter if exists
        users_collection.delete_one({"username": "testvoter"})
        
        # Remove all existing candidates
        candidates_collection.delete_many({})
        
        # Remove all existing votes
        votes_collection.delete_many({})
    else:
        # Remove test voter from in-memory
        if "testvoter" in users_db:
            del users_db["testvoter"]
        
        # Clear all candidates
        candidates_db.clear()
        
        # Clear all votes
        votes_db.clear()
        
        # Persist cleared state to disk when using file-backed fallback
        try:
            save_users()
            save_candidates()
            save_votes()
        except Exception:
            pass
    
    print("✅ All default data cleared - starting with empty database")
    return True

def requires_auth(f):
    """Decorator for routes requiring authentication"""
    @wraps(f)
    def decorated(*args, **kwargs):
        auth_header = request.headers.get('Authorization')
        if not auth_header:
            return jsonify({'success': False, 'message': 'Authentication required'}), 401
        return f(*args, **kwargs)
    return decorated

def scan_qr_from_image_file(file_path):
    """Scan QR code from an image file"""
    if not QR_SCANNING_AVAILABLE:
        return None
    
    try:
        img = cv2.imread(file_path)
        if img is None:
            return None
        
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        decoded_objects = decode(gray)
        
        if not decoded_objects:
            # Try with different preprocessing
            _, thresh = cv2.threshold(gray, 127, 255, cv2.THRESH_BINARY)
            decoded_objects = decode(thresh)
            
            if not decoded_objects:
                adaptive_thresh = cv2.adaptiveThreshold(gray, 255, 
                                                       cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                                       cv2.THRESH_BINARY, 11, 2)
                decoded_objects = decode(adaptive_thresh)
        
        for obj in decoded_objects:
            if obj.type == 'QRCODE':
                qr_data = obj.data.decode('utf-8')
                return qr_data
        
        return None
        
    except Exception as e:
        print(f"Error scanning QR code: {e}")
        return None

# Routes
@app.route('/')
def index():
    """Serve the main index page"""
    return app.send_static_file('index.html')

@app.route('/admin')
def admin_page():
    """Serve the admin page"""
    return app.send_static_file('admin.html')

@app.route('/voting')
def voting_page():
    """Serve the voting page"""
    return app.send_static_file('voting.html')

@app.route('/static/candidate_images/<filename>')
def serve_candidate_image(filename):
    """Serve candidate images"""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/api/test', methods=['GET'])
def test_api():
    """Test API endpoint"""
    return jsonify({
        'success': True,
        'message': 'Server is running',
        'mongodb': mongodb_connected,
        'qr_scanning': 'Available' if QR_SCANNING_AVAILABLE else 'Unavailable',
        'admin_account': 'Created' if get_admin("admin") else 'Not Found'
    })

@app.route('/api/register', methods=['POST'])
def register():
    """Register a new voter"""
    try:
        data = request.get_json()
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        fullName = data.get('fullName', '').strip()
        qr_data = data.get('qr_data', '').strip()
        
        if not username or not password:
            return jsonify({'success': False, 'message': 'Username and password are required'})
        
        # Check if username already exists
        existing_user = get_user(username)
        if existing_user:
            return jsonify({'success': False, 'message': 'Username already exists'})
        
        if len(password) < 6:
            return jsonify({'success': False, 'message': 'Password must be at least 6 characters'})
        
        # Decode Aadhaar QR data
        aadhaar_number = None
        decoded_data = None
        if qr_data:
            try:
                decoder = AadhaarQRDecoder(qr_data)
                decoded_data = decoder.decode()
                aadhaar_number = decoded_data.get('uid', '')
                
                # Check if Aadhaar is already registered
                if aadhaar_number:
                    # Check MongoDB
                    if mongodb_connected and users_collection is not None:
                        duplicate_user = users_collection.find_one({"aadhaar_number": aadhaar_number, "role": "voter"})
                    # Check in-memory
                    elif not mongodb_connected:
                        duplicate_user = next((u for u in users_db.values() if u.get('aadhaar_number') == aadhaar_number and u.get('role') == 'voter'), None)
                    else:
                        duplicate_user = None
                    
                    if duplicate_user:
                        return jsonify({
                            'success': False,
                            'message': 'Aadhaar number already registered',
                            'is_duplicate': True
                        })
            except Exception as e:
                return jsonify({'success': False, 'message': f'Invalid QR data: {str(e)}'})
        
        # Create user document
        user_data = {
            "username": username,
            "password": password,  # In production, hash this
            "role": "voter",
            "full_name": fullName or username,
            "aadhaar_number": aadhaar_number,
            "is_verified": bool(aadhaar_number),
            "has_voted": False,
            "qr_data": decoded_data if qr_data else None,
            "created_at": datetime.now()
        }
        
        # Save to MongoDB or in-memory
        user_id = create_user(user_data)
        
        if user_id:
            return jsonify({
                'success': True,
                'message': 'Registration successful',
                'user': {
                    'username': username,
                    'full_name': fullName or username,
                    'aadhaar_number': aadhaar_number,
                    'is_verified': bool(aadhaar_number)
                }
            })
        else:
            return jsonify({'success': False, 'message': 'Failed to create user account'})
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Registration failed: {str(e)}'})

@app.route('/api/login', methods=['POST'])
def login():
    """User login"""
    try:
        data = request.get_json()
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        
        if not username or not password:
            return jsonify({'success': False, 'message': 'Username and password are required'})
        
        user = get_user(username)
        if not user or user.get('role') != 'voter':
            return jsonify({'success': False, 'message': 'Invalid username or password'})
        
        if user.get('password') != password:  # In production, use proper password hashing
            return jsonify({'success': False, 'message': 'Invalid username or password'})
        
        return jsonify({
            'success': True,
            'message': 'Login successful',
            'user': {
                'username': username,
                'full_name': user.get('full_name', username),
                'aadhaar_number': user.get('aadhaar_number', ''),
                'is_verified': user.get('is_verified', False),
                'has_voted': user.get('has_voted', False),
                'qr_data': user.get('qr_data')
            }
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Login failed: {str(e)}'})

@app.route('/api/admin_login', methods=['POST'])
def admin_login():
    """Admin login with auto-creation if not exists"""
    try:
        data = request.get_json()
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        
        print(f"🔐 Admin login attempt: {username}")
        
        if not username or not password:
            return jsonify({'success': False, 'message': 'Username and password are required'})
        
        # Special case: if trying to login as admin with default password, create if not exists
        if username == "admin" and password == "admin123":
            admin = get_admin("admin")
            if not admin:
                print("⚠️ Admin account not found, creating on-demand...")
                admin_data = {
                    "username": "admin",
                    "password": "admin123",
                    "role": "admin",
                    "full_name": "System Administrator",
                    "created_at": datetime.now()
                }
                create_admin(admin_data)
                admin = admin_data
                print("✅ Admin account created on-demand")
            
            if admin.get('role') == 'admin' and admin.get('password') == password:
                token = f"admin_token_{username}_{datetime.now().timestamp()}"
                print(f"✅ Admin login successful: {username}")
                
                return jsonify({
                    'success': True,
                    'message': 'Admin login successful',
                    'admin': {
                        'username': username,
                        'full_name': admin.get('full_name', username)
                    },
                    'token': token
                })
        
        # Regular admin check
        admin = get_admin(username)
        if not admin or admin.get('role') != 'admin':
            print(f"❌ Admin not found or invalid role: {username}")
            return jsonify({'success': False, 'message': 'Invalid admin credentials'})
        
        if admin.get('password') != password:
            print(f"❌ Password mismatch for admin: {username}")
            return jsonify({'success': False, 'message': 'Invalid admin credentials'})
        
        token = f"admin_token_{username}_{datetime.now().timestamp()}"
        print(f"✅ Admin login successful: {username}")
        
        return jsonify({
            'success': True,
            'message': 'Admin login successful',
            'admin': {
                'username': username,
                'full_name': admin.get('full_name', username)
            },
            'token': token
        })
        
    except Exception as e:
        print(f"❌ Admin login error: {str(e)}")
        return jsonify({'success': False, 'message': f'Admin login failed: {str(e)}'})

@app.route('/api/validate_aadhaar', methods=['POST'])
def validate_aadhaar():
    """Validate Aadhaar QR data"""
    try:
        data = request.get_json()
        qr_data = data.get('qr_data', '').strip()
        
        if not qr_data:
            return jsonify({'success': False, 'message': 'QR data is required'})
        
        # Decode Aadhaar QR
        decoder = AadhaarQRDecoder(qr_data)
        decoded_data = decoder.decode()
        
        aadhaar_number = decoded_data.get('uid', '')
        
        if not aadhaar_number:
            return jsonify({'success': False, 'message': 'Invalid Aadhaar QR code'})
        
        # Check if Aadhaar is already registered
        is_duplicate = False
        existing_user = None
        
        if mongodb_connected and users_collection is not None:
            duplicate_user = users_collection.find_one({"aadhaar_number": aadhaar_number, "role": "voter"})
            if duplicate_user:
                is_duplicate = True
                existing_user = duplicate_user.get('username')
        elif not mongodb_connected:
            duplicate_user = next((u for u in users_db.values() if u.get('aadhaar_number') == aadhaar_number and u.get('role') == 'voter'), None)
            if duplicate_user:
                is_duplicate = True
                existing_user = duplicate_user.get('username')
        
        return jsonify({
            'success': True,
            'message': 'Aadhaar validated successfully',
            'aadhaar_number': aadhaar_number,
            'is_duplicate': is_duplicate,
            'existing_user': existing_user,
            'data': decoded_data
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Aadhaar validation failed: {str(e)}'})

@app.route('/api/scan_uploaded_image', methods=['POST'])
def scan_uploaded_image():
    """Scan QR code from uploaded image"""
    if not QR_SCANNING_AVAILABLE:
        return jsonify({
            'success': False,
            'message': 'QR scanning not available. Install opencv-python and pyzbar.'
        })
    
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'message': 'No file uploaded'})
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'message': 'No file selected'})
        
        # Save uploaded file temporarily
        temp_path = f"temp_{datetime.now().timestamp()}.jpg"
        file.save(temp_path)
        
        # Scan QR from image
        qr_data = scan_qr_from_image_file(temp_path)
        
        # Clean up temp file
        os.remove(temp_path)
        
        if qr_data:
            return jsonify({
                'success': True,
                'message': 'QR code scanned successfully',
                'qr_data': qr_data
            })
        else:
            return jsonify({'success': False, 'message': 'No QR code found in image'})
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Error processing image: {str(e)}'})

@app.route('/api/get_user_info', methods=['POST'])
def get_user_info():
    """Get user information"""
    try:
        data = request.get_json()
        username = data.get('username', '').strip()
        
        if not username:
            return jsonify({'success': False, 'message': 'Username is required'})
        
        user = get_user(username)
        if not user or user.get('role') != 'voter':
            return jsonify({'success': False, 'message': 'User not found'})
        
        # Get active candidates
        active_candidates = get_all_candidates()
        active_candidates = [c for c in active_candidates if c.get('is_active', True)]
        
        # Convert ObjectId to string for JSON serialization (MongoDB only)
        if mongodb_connected:
            for candidate in active_candidates:
                if '_id' in candidate:
                    candidate['_id'] = str(candidate['_id'])
        
        return jsonify({
            'success': True,
            'user': {
                'username': username,
                'full_name': user.get('full_name', username),
                'aadhaar_number': user.get('aadhaar_number', ''),
                'is_verified': user.get('is_verified', False),
                'has_voted': user.get('has_voted', False),
                'qr_data': user.get('qr_data')
            },
            'candidates': active_candidates
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Failed to get user info: {str(e)}'})

@app.route('/api/submit_vote', methods=['POST'])
def submit_vote():
    """Submit a vote"""
    try:
        data = request.get_json()
        username = data.get('username', '').strip()
        candidate_id = int(data.get('candidate_id', 0))
        
        if not username or not candidate_id:
            return jsonify({'success': False, 'message': 'Username and candidate ID are required'})
        
        user = get_user(username)
        if not user or user.get('role') != 'voter':
            return jsonify({'success': False, 'message': 'User not found'})
        
        if user.get('has_voted', False):
            return jsonify({'success': False, 'message': 'You have already voted'})
        
        if not user.get('is_verified', False):
            return jsonify({'success': False, 'message': 'Aadhaar verification required'})
        
        candidate = get_candidate(candidate_id)
        if not candidate or not candidate.get('is_active', True):
            return jsonify({'success': False, 'message': 'Invalid candidate'})
        
        # Record the vote
        vote_id = str(uuid.uuid4())
        vote_record = {
            'vote_id': vote_id,
            'username': username,
            'candidate_id': candidate_id,
            'candidate_name': candidate.get('name'),
            'candidate_party': candidate.get('party'),
            'voted_at': datetime.now(),
            'timestamp': datetime.now().isoformat()
        }
        
        create_vote(vote_record)
        
        # Update user record
        update_user(username, {'has_voted': True})
        
        # Update candidate votes
        current_votes = candidate.get('votes', 0)
        update_candidate(candidate_id, {'votes': current_votes + 1})
        
        return jsonify({
            'success': True,
            'message': 'Vote submitted successfully',
            'vote_id': vote_id,
            'candidate_name': candidate.get('name')
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Failed to submit vote: {str(e)}'})

@app.route('/api/get_vote_history', methods=['POST'])
def get_vote_history():
    """Get user's vote history"""
    try:
        data = request.get_json()
        username = data.get('username', '').strip()
        
        if not username:
            return jsonify({'success': False, 'message': 'Username is required'})
        
        user = get_user(username)
        if not user:
            return jsonify({'success': False, 'message': 'User not found'})
        
        # Find user's vote
        user_vote = None
        if mongodb_connected and votes_collection is not None:
            user_vote = votes_collection.find_one({"username": username})
            if user_vote and '_id' in user_vote:
                user_vote['_id'] = str(user_vote['_id'])
        elif not mongodb_connected:
            user_vote = next((v for v in votes_db if v['username'] == username), None)
        
        return jsonify({
            'success': True,
            'has_voted': user.get('has_voted', False),
            'vote': user_vote
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Failed to get vote history: {str(e)}'})

# Admin API endpoints
@app.route('/api/admin/get_results', methods=['GET'])
def admin_get_results():
    """Admin: Get voting results"""
    try:
        # Calculate statistics
        total_voters = get_total_voters()
        total_votes = get_total_votes()
        total_candidates = get_total_candidates()
        
        voting_percentage = 0
        if total_voters > 0:
            voting_percentage = round((total_votes / total_voters) * 100, 1)
        
        # Get all candidates
        all_candidates = get_all_candidates()
        # Convert ObjectId to string for JSON serialization (MongoDB only)
        if mongodb_connected:
            for candidate in all_candidates:
                if '_id' in candidate:
                    candidate['_id'] = str(candidate['_id'])
        
        return jsonify({
            'success': True,
            'statistics': {
                'total_voters': total_voters,
                'total_votes': total_votes,
                'voting_percentage': voting_percentage,
                'total_candidates': total_candidates
            },
            'all_candidates': all_candidates
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Failed to get results: {str(e)}'})

@app.route('/api/admin/add_candidate', methods=['POST'])
def admin_add_candidate():
    """Admin: Add a new candidate with optional image"""
    try:
        name = request.form.get('name', '').strip()
        party = request.form.get('party', '').strip()
        
        if not name or not party:
            return jsonify({'success': False, 'message': 'Candidate name and party are required'})
        
        # Generate new candidate ID
        all_candidates = get_all_candidates()
        existing_ids = [c.get('candidate_id', 0) for c in all_candidates]
        new_id = max(existing_ids) + 1 if existing_ids else 1
        
        # Handle image upload
        image_url = None
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename and allowed_file(file.filename):
                # Create upload folder if it doesn't exist
                if not os.path.exists(app.config['UPLOAD_FOLDER']):
                    os.makedirs(app.config['UPLOAD_FOLDER'])
                
                # Generate secure filename
                filename = secure_filename(f"candidate_{new_id}_{file.filename}")
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(file_path)
                image_url = f'/static/candidate_images/{filename}'
        
        candidate_data = {
            'candidate_id': new_id,
            'name': name,
            'party': party,
            'image_url': image_url,
            'is_active': True,
            'votes': 0,
            'created_at': datetime.now()
        }
        
        candidate_id = create_candidate(candidate_data)
        
        if candidate_id:
            return jsonify({
                'success': True,
                'message': f'Candidate {name} added successfully',
                'candidate_id': new_id,
                'image_url': image_url
            })
        else:
            return jsonify({'success': False, 'message': 'Failed to add candidate'})
        
    except Exception as e:
        print(f"Error adding candidate: {e}")
        return jsonify({'success': False, 'message': f'Failed to add candidate: {str(e)}'})

@app.route('/api/admin/remove_candidate', methods=['POST'])
def admin_remove_candidate():
    """Admin: Remove a candidate"""
    try:
        data = request.get_json()
        candidate_id = data.get('candidate_id')
        
        if candidate_id is None:
            return jsonify({'success': False, 'message': 'Candidate ID is required'})
        
        # Convert to integer if it's a string
        try:
            candidate_id = int(candidate_id)
        except (ValueError, TypeError):
            return jsonify({'success': False, 'message': 'Invalid candidate ID format'})
        
        candidate = get_candidate(candidate_id)
        if not candidate:
            return jsonify({'success': False, 'message': 'Candidate not found'})
        
        candidate_name = candidate.get('name', 'Unknown')
        
        # Delete the candidate completely (not just mark as inactive)
        deleted = delete_candidate(candidate_id)
        
        if deleted:
            return jsonify({
                'success': True,
                'message': f'Candidate {candidate_name} removed successfully'
            })
        else:
            return jsonify({'success': False, 'message': 'Failed to remove candidate'})
        
    except Exception as e:
        print(f"Error removing candidate: {e}")
        return jsonify({'success': False, 'message': f'Failed to remove candidate: {str(e)}'})

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({'success': False, 'message': 'Endpoint not found'}), 404

@app.errorhandler(500)
def server_error(error):
    return jsonify({'success': False, 'message': 'Internal server error'}), 500

# Main entry point
if __name__ == '__main__':
    print("="*60)
    print("ONLINE VOTING SYSTEM")
    print("="*60)
    print(f"📊 MongoDB: {'✅ Connected' if mongodb_connected else '❌ Not Connected'}")
    print(f"📷 QR Scanning: {'✅ Available' if QR_SCANNING_AVAILABLE else '❌ Unavailable'}")
    
    # Initialize admin but do NOT clear existing data by default (preserve persisted data)
    admin_created = init_default_admin()
    # To explicitly reset default test data, set environment variable RESET_DEFAULTS=1
    if os.environ.get('RESET_DEFAULTS') == '1':
        init_default_data()  # Only run when explicitly requested
    
    print("\n🔑 ADMIN LOGIN CREDENTIALS:")
    if admin_created:
        print("   Username: admin")
        print("   Password: admin123")
    else:
        print("   ❌ Admin account could not be created")
    
    print("\n📝 IMPORTANT:")
    print("   - All default candidates and test data have been removed")
    print("   - You can add new candidates from the admin panel")
    print("   - No default test voter exists (register new voters)")
    
    print("\n🌐 Server URL: http://127.0.0.1:5000")
    print("   Admin Panel: http://127.0.0.1:5000/admin")
    print("="*60)
    
    # Create necessary directories
    if not os.path.exists('temp'):
        os.makedirs('temp')
    
    # Create candidate images directory
    if not os.path.exists(app.config['UPLOAD_FOLDER']):
        os.makedirs(app.config['UPLOAD_FOLDER'])
    
    # Run the Flask app
    app.run(host='127.0.0.1', port=5000, debug=True, use_reloader=False)