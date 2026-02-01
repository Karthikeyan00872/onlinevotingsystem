# app.py
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_from_directory, send_file
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
import io

# Aadhaar XML Decoder
class AadhaarXMLDecoder:
    """Class to decode Aadhaar XML file data"""
    
    def __init__(self, xml_content: str):
        self.xml_content = xml_content.strip()
        self.decoded_data = {}
        self.profile_photo_base64 = None
        
    def decode(self) -> dict:
        """Decode XML data and extract information"""
        try:
            # Parse XML
            root = ET.fromstring(self.xml_content)
            
            # Find the OfflinePaperlessKyc element
            kyc_element = root.find('.//OfflinePaperlessKyc')
            if kyc_element is None:
                kyc_element = root
            
            # Extract reference ID (may contain Aadhaar info)
            reference_id = kyc_element.get('referenceId', '')
            self.decoded_data['reference_id'] = reference_id
            
            # Try to extract Aadhaar number from reference ID
            aadhaar_number = self._extract_aadhaar_from_reference(reference_id)
            
            # Extract UidData
            uid_data = kyc_element.find('UidData')
            if uid_data is None:
                raise ValueError("No UidData found in XML")
            
            # Extract Poi (Proof of Identity)
            poi = uid_data.find('Poi')
            if poi is not None:
                # The Aadhaar number might be in 'uid' attribute or need to be extracted from signature
                uid_from_poi = poi.get('uid', '')
                if uid_from_poi:
                    aadhaar_number = uid_from_poi
                
                self.decoded_data['name'] = poi.get('name', '')
                self.decoded_data['dob'] = poi.get('dob', '')
                self.decoded_data['gender'] = poi.get('gender', '')
                
                # The 'e' and 'm' attributes might contain encrypted data
                self.decoded_data['e'] = poi.get('e', '')
                self.decoded_data['m'] = poi.get('m', '')
            
            # Extract Poa (Proof of Address)
            poa = uid_data.find('Poa')
            if poa is not None:
                self.decoded_data['careof'] = poa.get('careof', '')
                self.decoded_data['house'] = poa.get('house', '')
                self.decoded_data['street'] = poa.get('street', '')
                self.decoded_data['landmark'] = poa.get('landmark', '')
                self.decoded_data['locality'] = poa.get('loc', '') or poa.get('locality', '')
                self.decoded_data['vtc'] = poa.get('vtc', '')
                self.decoded_data['subdist'] = poa.get('subdist', '')
                self.decoded_data['district'] = poa.get('dist', '')
                self.decoded_data['state'] = poa.get('state', '')
                self.decoded_data['pincode'] = poa.get('pc', '')
                self.decoded_data['country'] = poa.get('country', 'India')
            
            # Extract Pht (Photo)
            pht = uid_data.find('Pht')
            if pht is not None and pht.text:
                self.profile_photo_base64 = pht.text
                # Store photo in decoded_data as well
                self.decoded_data['photo_base64'] = pht.text
            
            # Store the extracted Aadhaar number
            self.decoded_data['aadhaar_number'] = aadhaar_number
            
            return self.decoded_data
            
        except ET.ParseError as e:
            raise ValueError(f"Failed to parse XML: {e}")
        except Exception as e:
            raise ValueError(f"Error processing Aadhaar XML: {e}")
    
    def _extract_aadhaar_from_reference(self, reference_id: str) -> str:
        """Extract Aadhaar number from reference ID"""
        if not reference_id:
            return ""
        
        # Reference ID format might contain Aadhaar number
        # Try different patterns to extract Aadhaar
        patterns = [
            r'\d{12}',  # 12 digit Aadhaar
            r'\d{16}',  # 16 digit reference
            r'\d+',     # Any digits
        ]
        
        for pattern in patterns:
            matches = re.findall(pattern, reference_id)
            if matches:
                # Take the longest match
                longest_match = max(matches, key=len)
                if len(longest_match) >= 12:  # At least 12 digits for Aadhaar
                    return longest_match
        
        return ""
    
    def get_profile_photo(self):
        """Get profile photo as base64 string"""
        return self.profile_photo_base64
    
    def save_profile_photo(self, file_path):
        """Save profile photo to file"""
        if self.profile_photo_base64:
            try:
                # Remove data URL prefix if present
                photo_data = self.profile_photo_base64
                if 'base64,' in photo_data:
                    photo_data = photo_data.split('base64,')[1]
                
                # Decode base64 and save
                with open(file_path, 'wb') as f:
                    f.write(base64.b64decode(photo_data))
                return True
            except Exception as e:
                print(f"Error saving profile photo: {e}")
                return False
        return False

# Initialize Flask app
app = Flask(__name__, static_folder='.', static_url_path='')
app.secret_key = 'your-secret-key-here-change-in-production'
CORS(app)

# Configuration for file uploads
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['UPLOAD_FOLDER'] = 'static/uploads'
app.config['PROFILE_PHOTOS_FOLDER'] = 'static/profile_photos'
app.config['ALLOWED_EXTENSIONS'] = {'xml', 'XML'}
app.config['ALLOWED_PHOTO_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif'}

def allowed_file(filename, file_type='xml'):
    """Check if file extension is allowed"""
    if file_type == 'xml':
        return '.' in filename and \
               filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']
    else:  # photo
        return '.' in filename and \
               filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_PHOTO_EXTENSIONS']

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

    # Load candidates (keys stored as strings in JSON, convert to int)
    raw_candidates = _load_json(CANDIDATES_FILE, {})
    try:
        candidates_db = {int(k): v for k, v in raw_candidates.items()} if isinstance(raw_candidates, dict) else {}
    except Exception:
        candidates_db = {}

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

def get_user_by_aadhaar(aadhaar_number):
    """Get user by Aadhaar number"""
    if mongodb_connected and users_collection is not None:
        return users_collection.find_one({"aadhaar_number": aadhaar_number, "role": "voter"})
    elif not mongodb_connected:
        return next((u for u in users_db.values() if u.get('aadhaar_number') == aadhaar_number and u.get('role') == 'voter'), None)
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

def save_profile_photo_from_xml(xml_content, username):
    """Extract and save profile photo from XML"""
    try:
        decoder = AadhaarXMLDecoder(xml_content)
        decoded_data = decoder.decode()
        
        photo_base64 = decoder.get_profile_photo()
        if not photo_base64:
            return None
        
        # Create profile photos directory if it doesn't exist
        if not os.path.exists(app.config['PROFILE_PHOTOS_FOLDER']):
            os.makedirs(app.config['PROFILE_PHOTOS_FOLDER'])
        
        # Generate filename
        filename = f"{username}_profile.jpg"
        filepath = os.path.join(app.config['PROFILE_PHOTOS_FOLDER'], filename)
        
        # Save photo
        if decoder.save_profile_photo(filepath):
            return f"/static/profile_photos/{filename}"
        
        return None
    except Exception as e:
        print(f"Error saving profile photo: {e}")
        return None

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

@app.route('/static/profile_photos/<filename>')
def serve_profile_photo(filename):
    """Serve profile photos"""
    return send_from_directory(app.config['PROFILE_PHOTOS_FOLDER'], filename)

@app.route('/static/uploads/<filename>')
def serve_upload(filename):
    """Serve uploaded files"""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/api/test', methods=['GET'])
def test_api():
    """Test API endpoint"""
    return jsonify({
        'success': True,
        'message': 'Server is running',
        'mongodb': mongodb_connected,
        'admin_account': 'Created' if get_admin("admin") else 'Not Found'
    })

@app.route('/api/register', methods=['POST'])
def register():
    """Register a new voter with Aadhaar XML file"""
    try:
        # Check if XML file is uploaded
        if 'aadhaar_xml' not in request.files:
            return jsonify({'success': False, 'message': 'No Aadhaar XML file uploaded'})
        
        xml_file = request.files['aadhaar_xml']
        
        if xml_file.filename == '':
            return jsonify({'success': False, 'message': 'No XML file selected'})
        
        if not allowed_file(xml_file.filename, 'xml'):
            return jsonify({'success': False, 'message': 'Invalid file type. Only XML files are allowed'})
        
        # Read XML content
        xml_content = xml_file.read().decode('utf-8')
        
        # Parse form data
        username = request.form.get('username', '').strip()
        password = request.form.get('password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()
        full_name = request.form.get('full_name', '').strip()
        
        # Validate inputs
        if not username or not password:
            return jsonify({'success': False, 'message': 'Username and password are required'})
        
        if len(username) < 3:
            return jsonify({'success': False, 'message': 'Username must be at least 3 characters'})
        
        if len(password) < 6:
            return jsonify({'success': False, 'message': 'Password must be at least 6 characters'})
        
        if password != confirm_password:
            return jsonify({'success': False, 'message': 'Passwords do not match'})
        
        # Check if username already exists
        existing_user = get_user(username)
        if existing_user:
            return jsonify({'success': False, 'message': 'Username already exists'})
        
        # Decode Aadhaar XML
        try:
            decoder = AadhaarXMLDecoder(xml_content)
            decoded_data = decoder.decode()
            
            # Extract Aadhaar number
            aadhaar_number = decoded_data.get('aadhaar_number', '')
            
            if not aadhaar_number or len(aadhaar_number) < 12:
                return jsonify({'success': False, 'message': 'Could not extract valid Aadhaar number from XML. Please make sure you uploaded the correct Aadhaar XML file.'})
            
            # Check if Aadhaar is already registered
            existing_user_with_aadhaar = get_user_by_aadhaar(aadhaar_number)
            if existing_user_with_aadhaar:
                return jsonify({
                    'success': False,
                    'message': 'Aadhaar number already registered',
                    'is_duplicate': True,
                    'existing_user': existing_user_with_aadhaar.get('username')
                })
            
            # Save profile photo from XML
            profile_photo_url = save_profile_photo_from_xml(xml_content, username)
            
            # Create user document
            user_data = {
                "username": username,
                "password": password,
                "role": "voter",
                "full_name": full_name or decoded_data.get('name', username),
                "aadhaar_number": aadhaar_number,
                "is_verified": True,
                "has_voted": False,
                "aadhaar_data": decoded_data,
                "profile_photo_url": profile_photo_url,
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
                        'full_name': full_name or decoded_data.get('name', username),
                        'aadhaar_number': aadhaar_number,
                        'is_verified': True,
                        'profile_photo_url': profile_photo_url
                    }
                })
            else:
                return jsonify({'success': False, 'message': 'Failed to create user account'})
                
        except Exception as e:
            return jsonify({'success': False, 'message': f'Invalid Aadhaar XML: {str(e)}'})
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Registration failed: {str(e)}'})

@app.route('/api/validate_aadhaar_xml', methods=['POST'])
def validate_aadhaar_xml():
    """Validate Aadhaar XML file"""
    try:
        if 'xml_file' not in request.files:
            return jsonify({'success': False, 'message': 'No XML file uploaded'})
        
        xml_file = request.files['xml_file']
        
        if xml_file.filename == '':
            return jsonify({'success': False, 'message': 'No XML file selected'})
        
        if not allowed_file(xml_file.filename, 'xml'):
            return jsonify({'success': False, 'message': 'Invalid file type. Only XML files are allowed'})
        
        # Read XML content
        xml_content = xml_file.read().decode('utf-8')
        
        # Decode Aadhaar XML
        decoder = AadhaarXMLDecoder(xml_content)
        decoded_data = decoder.decode()
        
        aadhaar_number = decoded_data.get('aadhaar_number', '')
        
        if not aadhaar_number or len(aadhaar_number) < 12:
            return jsonify({'success': False, 'message': 'Could not extract valid Aadhaar number from XML. Please make sure you uploaded the correct Aadhaar XML file.'})
        
        # Check if Aadhaar is already registered
        existing_user = get_user_by_aadhaar(aadhaar_number)
        is_duplicate = existing_user is not None
        
        # Get profile photo if available
        profile_photo_base64 = decoder.get_profile_photo()
        profile_photo_url = None
        
        return jsonify({
            'success': True,
            'message': 'Aadhaar XML validated successfully',
            'aadhaar_number': aadhaar_number,
            'is_duplicate': is_duplicate,
            'existing_user': existing_user.get('username') if existing_user else None,
            'data': decoded_data,
            'has_photo': bool(profile_photo_base64)
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Aadhaar XML validation failed: {str(e)}'})

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
        
        if user.get('password') != password:
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
                'profile_photo_url': user.get('profile_photo_url'),
                'aadhaar_data': user.get('aadhaar_data', {})
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
                'profile_photo_url': user.get('profile_photo_url'),
                'aadhaar_data': user.get('aadhaar_data', {})
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
            if file and file.filename and allowed_file(file.filename, 'photo'):
                # Create upload folder if it doesn't exist
                if not os.path.exists(app.config['UPLOAD_FOLDER']):
                    os.makedirs(app.config['UPLOAD_FOLDER'])
                
                # Generate secure filename
                filename = secure_filename(f"candidate_{new_id}_{file.filename}")
                file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
                file.save(file_path)
                image_url = f'/static/uploads/{filename}'
        
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

# Alternative registration method for testing
@app.route('/api/register_without_aadhaar', methods=['POST'])
def register_without_aadhaar():
    """Alternative registration for testing without Aadhaar XML"""
    try:
        data = request.get_json()
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        full_name = data.get('full_name', '').strip()
        
        if not username or not password:
            return jsonify({'success': False, 'message': 'Username and password are required'})
        
        if len(username) < 3:
            return jsonify({'success': False, 'message': 'Username must be at least 3 characters'})
        
        if len(password) < 6:
            return jsonify({'success': False, 'message': 'Password must be at least 6 characters'})
        
        # Check if username already exists
        existing_user = get_user(username)
        if existing_user:
            return jsonify({'success': False, 'message': 'Username already exists'})
        
        # Create user document without Aadhaar verification
        user_data = {
            "username": username,
            "password": password,
            "role": "voter",
            "full_name": full_name or username,
            "aadhaar_number": f"TEST_{username}",  # Test Aadhaar number
            "is_verified": False,  # Not verified since no Aadhaar XML
            "has_voted": False,
            "aadhaar_data": {},
            "profile_photo_url": None,
            "created_at": datetime.now()
        }
        
        # Save to MongoDB or in-memory
        user_id = create_user(user_data)
        
        if user_id:
            return jsonify({
                'success': True,
                'message': 'Registration successful (TEST MODE - No Aadhaar verification)',
                'user': {
                    'username': username,
                    'full_name': full_name or username,
                    'aadhaar_number': f"TEST_{username}",
                    'is_verified': False
                }
            })
        else:
            return jsonify({'success': False, 'message': 'Failed to create user account'})
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Registration failed: {str(e)}'})

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
    print("ONLINE VOTING SYSTEM WITH AADHAAR XML INTEGRATION")
    print("="*60)
    print(f"📊 MongoDB: {'✅ Connected' if mongodb_connected else '❌ Not Connected'}")
    
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
    print("   - Registration requires Aadhaar XML file upload")
    print("   - Download your Aadhaar XML from: https://tathya.uidai.gov.in/access/login?role=resident")
    print("   - Profile photos are automatically extracted from XML")
    print("   - For testing without Aadhaar, use the alternative registration endpoint")
    
    print("\n🌐 Server URL: http://127.0.0.1:5000")
    print("   Admin Panel: http://127.0.0.1:5000/admin")
    print("="*60)
    
    # Create necessary directories
    if not os.path.exists('temp'):
        os.makedirs('temp')
    
    # Create upload directories
    for folder in [app.config['UPLOAD_FOLDER'], app.config['PROFILE_PHOTOS_FOLDER']]:
        if not os.path.exists(folder):
            os.makedirs(folder)
    
    # Run the Flask app
    app.run(host='127.0.0.1', port=5000, debug=True, use_reloader=False)