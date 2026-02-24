# app.py
from flask import Flask, render_template, request, jsonify, redirect, url_for, session, send_from_directory, send_file
from flask_cors import CORS
from pymongo import MongoClient
from bson import ObjectId, Binary
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
import logging
import sys

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout)
    ]
)
logger = logging.getLogger(__name__)

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

# Initialize Flask app - serve frontend files from sibling `frontend` folder
app = Flask(__name__,
            static_folder='../frontend',
            static_url_path='',
            template_folder='../frontend')

# Get secret key from environment variable
app.secret_key = os.environ.get('SECRET_KEY', os.urandom(24).hex())
CORS(app)

# Configuration for file uploads
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size
app.config['ALLOWED_EXTENSIONS'] = {'xml', 'XML'}
app.config['ALLOWED_IMAGE_EXTENSIONS'] = {'png', 'jpg', 'jpeg', 'gif'}

def allowed_file(filename, file_type='xml'):
    """Check if file extension is allowed"""
    if file_type == 'xml':
        return '.' in filename and \
               filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']
    else:  # image
        return '.' in filename and \
               filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_IMAGE_EXTENSIONS']

def safe_object_id(id_string):
    """Safely convert string to ObjectId"""
    try:
        return ObjectId(id_string)
    except:
        return None

# MongoDB connection
def get_mongodb_connection():
    """Get MongoDB connection from environment variables"""
    try:
        # Get MongoDB URI from environment variable (set in Render)
        mongodb_uri = os.environ.get('MONGODB_URI')
        
        if not mongodb_uri:
            logger.warning("⚠️ MONGODB_URI not set!")
            return {
                'connected': False,
                'users': None,
                'admins': None,
                'candidates': None,
                'votes': None,
                'images': None
            }
        
        # Add connection options for better reliability
        client = MongoClient(
            mongodb_uri, 
            serverSelectionTimeoutMS=5000,
            connectTimeoutMS=30000,
            socketTimeoutMS=30000,
            maxPoolSize=50,
            retryWrites=True
        )
        
        # Test the connection
        client.server_info()
        
        db = client['online_voting']
        
        logger.info("✅ Successfully connected to MongoDB")
        return {
            'connected': True,
            'users': db['users'],
            'admins': db['admins'],
            'candidates': db['candidates'],
            'votes': db['votes'],
            'images': db['images']
        }
    except Exception as e:
        logger.error(f"❌ Error connecting to MongoDB: {e}")
        return {
            'connected': False,
            'users': None,
            'admins': None,
            'candidates': None,
            'votes': None,
            'images': None
        }

# Initialize MongoDB connection
mongo = get_mongodb_connection()
mongodb_connected = mongo['connected']
users_collection = mongo['users']
admins_collection = mongo['admins']
candidates_collection = mongo['candidates']
votes_collection = mongo['votes']
images_collection = mongo['images']

# Image storage functions
def store_image_in_mongodb(image_data, image_type='profile'):
    """Store image in MongoDB and return image ID"""
    if not mongodb_connected or images_collection is None:
        return None
    
    try:
        # Convert image to base64 if it's binary
        if isinstance(image_data, bytes):
            image_b64 = base64.b64encode(image_data).decode('utf-8')
        else:
            # Assume it's already base64 string
            image_b64 = image_data
        
        # Create image document
        image_doc = {
            'data': image_b64,
            'type': image_type,
            'created_at': datetime.now()
        }
        
        result = images_collection.insert_one(image_doc)
        return str(result.inserted_id)
    except Exception as e:
        logger.error(f"Error storing image in MongoDB: {e}")
        return None

def get_image_from_mongodb(image_id):
    """Retrieve image from MongoDB by ID"""
    if not mongodb_connected or images_collection is None:
        return None
    
    try:
        obj_id = safe_object_id(image_id)
        if not obj_id:
            return None
            
        image_doc = images_collection.find_one({'_id': obj_id})
        if image_doc:
            return image_doc.get('data')
        return None
    except Exception as e:
        logger.error(f"Error retrieving image from MongoDB: {e}")
        return None

# Helper functions for MongoDB/fallback
def get_user(username):
    """Get user from MongoDB"""
    try:
        if mongodb_connected and users_collection is not None:
            return users_collection.find_one({"username": username})
        return None
    except Exception as e:
        logger.error(f"Error getting user: {e}")
        return None

def get_user_by_aadhaar(aadhaar_number):
    """Get user by Aadhaar number"""
    try:
        if mongodb_connected and users_collection is not None:
            return users_collection.find_one({"aadhaar_number": aadhaar_number, "role": "voter"})
        return None
    except Exception as e:
        logger.error(f"Error getting user by aadhaar: {e}")
        return None

def get_admin(username):
    """Get admin from MongoDB"""
    try:
        if mongodb_connected and admins_collection is not None:
            return admins_collection.find_one({"username": username})
        return None
    except Exception as e:
        logger.error(f"Error getting admin: {e}")
        return None

def get_candidate(candidate_id):
    """Get candidate from MongoDB"""
    try:
        candidate_id = int(candidate_id) if isinstance(candidate_id, (str, float)) else candidate_id
        if mongodb_connected and candidates_collection is not None:
            return candidates_collection.find_one({"candidate_id": candidate_id})
        return None
    except Exception as e:
        logger.error(f"Error getting candidate: {e}")
        return None

def get_all_candidates():
    """Get all candidates from MongoDB"""
    try:
        if mongodb_connected and candidates_collection is not None:
            return list(candidates_collection.find({}))
        return []
    except Exception as e:
        logger.error(f"Error getting all candidates: {e}")
        return []

def get_all_votes():
    """Get all votes from MongoDB"""
    try:
        if mongodb_connected and votes_collection is not None:
            return list(votes_collection.find({}))
        return []
    except Exception as e:
        logger.error(f"Error getting all votes: {e}")
        return []

def create_user(user_data):
    """Create new user in MongoDB"""
    try:
        if mongodb_connected and users_collection is not None:
            result = users_collection.insert_one(user_data)
            return result.inserted_id
        return None
    except Exception as e:
        logger.error(f"Error creating user: {e}")
        return None

def create_admin(admin_data):
    """Create new admin in MongoDB"""
    try:
        if mongodb_connected and admins_collection is not None:
            result = admins_collection.insert_one(admin_data)
            return result.inserted_id
        return None
    except Exception as e:
        logger.error(f"Error creating admin: {e}")
        return None

def create_candidate(candidate_data):
    """Create new candidate in MongoDB"""
    try:
        if mongodb_connected and candidates_collection is not None:
            result = candidates_collection.insert_one(candidate_data)
            return result.inserted_id
        return None
    except Exception as e:
        logger.error(f"Error creating candidate: {e}")
        return None

def create_vote(vote_data):
    """Create new vote in MongoDB"""
    try:
        if mongodb_connected and votes_collection is not None:
            result = votes_collection.insert_one(vote_data)
            return result.inserted_id
        return None
    except Exception as e:
        logger.error(f"Error creating vote: {e}")
        return None

def update_user(username, update_data):
    """Update user in MongoDB"""
    try:
        if mongodb_connected and users_collection is not None:
            users_collection.update_one({"username": username}, {"$set": update_data})
            return True
        return False
    except Exception as e:
        logger.error(f"Error updating user: {e}")
        return False

def update_candidate(candidate_id, update_data):
    """Update candidate in MongoDB"""
    try:
        candidate_id = int(candidate_id) if isinstance(candidate_id, (str, float)) else candidate_id
        if mongodb_connected and candidates_collection is not None:
            candidates_collection.update_one({"candidate_id": candidate_id}, {"$set": update_data})
            return True
        return False
    except Exception as e:
        logger.error(f"Error updating candidate: {e}")
        return False

def delete_candidate(candidate_id):
    """Delete candidate from MongoDB"""
    try:
        candidate_id = int(candidate_id) if isinstance(candidate_id, (str, float)) else candidate_id
        if mongodb_connected and candidates_collection is not None:
            result = candidates_collection.delete_one({"candidate_id": candidate_id})
            return result.deleted_count > 0
        return False
    except Exception as e:
        logger.error(f"Error deleting candidate: {e}")
        return False

def get_total_voters():
    """Get total number of voters"""
    try:
        if mongodb_connected and users_collection is not None:
            return users_collection.count_documents({"role": "voter"})
        return 0
    except Exception as e:
        logger.error(f"Error getting total voters: {e}")
        return 0

def get_total_votes():
    """Get total number of votes"""
    try:
        if mongodb_connected and votes_collection is not None:
            return votes_collection.count_documents({})
        return 0
    except Exception as e:
        logger.error(f"Error getting total votes: {e}")
        return 0

def get_total_candidates():
    """Get total number of active candidates"""
    try:
        if mongodb_connected and candidates_collection is not None:
            return candidates_collection.count_documents({"is_active": True})
        return 0
    except Exception as e:
        logger.error(f"Error getting total candidates: {e}")
        return 0

# Initialize default admin
def init_default_admin():
    """Initialize default admin account"""
    logger.info("🔍 Checking for admin account...")
    
    if mongodb_connected and admins_collection is not None:
        try:
            admin = admins_collection.find_one({"username": "admin"})
            if admin:
                logger.info("✅ Admin account found in MongoDB")
                return True
            
            logger.info("⚠️ Admin account not found, creating...")
            admin_data = {
                "username": "admin",
                "password": "admin123",
                "role": "admin",
                "full_name": "System Administrator",
                "created_at": datetime.now()
            }
            admins_collection.insert_one(admin_data)
            logger.info("✅ Admin account created in MongoDB")
            return True
        except Exception as e:
            logger.error(f"❌ Failed to create admin in MongoDB: {e}")
            return False
    
    return False

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
    return render_template('index.html')

@app.route('/admin')
def admin_page():
    """Serve the admin page"""
    return render_template('admin.html')

@app.route('/voting')
def voting_page():
    """Serve the voting page"""
    return render_template('voting.html')

# New route to serve images from MongoDB
@app.route('/api/image/<image_id>')
def get_image(image_id):
    """Serve image from MongoDB"""
    try:
        # Validate ObjectId
        obj_id = safe_object_id(image_id)
        if not obj_id:
            return jsonify({'success': False, 'message': 'Invalid image ID'}), 400
            
        image_data = get_image_from_mongodb(image_id)
        if image_data:
            # Determine content type based on image data
            if image_data.startswith('data:image/'):
                # Extract base64 data from data URL
                if 'base64,' in image_data:
                    image_data = image_data.split('base64,')[1]
            
            # Decode base64
            try:
                image_bytes = base64.b64decode(image_data)
            except:
                return jsonify({'success': False, 'message': 'Invalid image data'}), 400
            
            # Determine content type (default to JPEG)
            content_type = 'image/jpeg'
            if image_data.startswith('iVBOR'):  # PNG base64 signature
                content_type = 'image/png'
            elif image_data.startswith('/9j/'):  # JPEG base64 signature
                content_type = 'image/jpeg'
            elif image_data.startswith('R0lGOD'):  # GIF base64 signature
                content_type = 'image/gif'
            
            return send_file(
                io.BytesIO(image_bytes),
                mimetype=content_type,
                as_attachment=False
            )
        return jsonify({'success': False, 'message': 'Image not found'}), 404
    except Exception as e:
        logger.error(f"Error serving image: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/test', methods=['GET'])
def test_api():
    """Test API endpoint"""
    return jsonify({
        'success': True,
        'message': 'Server is running',
        'mongodb': mongodb_connected,
        'admin_account': 'Created' if get_admin("admin") else 'Not Found',
        'environment': os.environ.get('RENDER', 'not set')
    })

@app.route('/api/debug/mongodb', methods=['GET'])
def debug_mongodb():
    """Debug MongoDB connection"""
    try:
        if mongodb_connected and users_collection is not None:
            # Try to count documents
            user_count = users_collection.count_documents({})
            admin_count = admins_collection.count_documents({}) if admins_collection is not None else 0
            
            # Test write operation
            test_doc = {"test": "connection", "timestamp": datetime.now()}
            test_result = users_collection.insert_one(test_doc)
            users_collection.delete_one({"_id": test_result.inserted_id})
            
            return jsonify({
                'success': True,
                'connected': True,
                'user_count': user_count,
                'admin_count': admin_count,
                'mongodb_uri_set': bool(os.environ.get('MONGODB_URI')),
                'write_test': 'passed'
            })
        else:
            return jsonify({
                'success': False,
                'connected': False,
                'mongodb_uri_set': bool(os.environ.get('MONGODB_URI')),
                'message': 'MongoDB connection failed'
            })
    except Exception as e:
        logger.error(f"Debug MongoDB error: {e}")
        return jsonify({
            'success': False,
            'error': str(e)
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
            
            # Store profile photo from XML in MongoDB
            profile_photo_url = None
            profile_photo_base64 = decoder.get_profile_photo()
            if profile_photo_base64:
                image_id = store_image_in_mongodb(profile_photo_base64, 'profile')
                if image_id:
                    profile_photo_url = f'/api/image/{image_id}'
            
            # Create user document
            user_data = {
                "username": username,
                "password": password,  # In production, hash this!
                "role": "voter",
                "full_name": full_name or decoded_data.get('name', username),
                "aadhaar_number": aadhaar_number,
                "is_verified": True,
                "has_voted": False,
                "aadhaar_data": decoded_data,
                "profile_photo_url": profile_photo_url,
                "profile_photo_id": image_id if profile_photo_base64 else None,
                "created_at": datetime.now()
            }
            
            # Save to MongoDB
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
            logger.error(f"Aadhaar XML decoding error: {e}")
            return jsonify({'success': False, 'message': f'Invalid Aadhaar XML: {str(e)}'})
        
    except Exception as e:
        logger.error(f"Registration error: {e}")
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
        has_photo = bool(profile_photo_base64)
        
        return jsonify({
            'success': True,
            'message': 'Aadhaar XML validated successfully',
            'aadhaar_number': aadhaar_number,
            'is_duplicate': is_duplicate,
            'existing_user': existing_user.get('username') if existing_user else None,
            'data': decoded_data,
            'has_photo': has_photo
        })
        
    except Exception as e:
        logger.error(f"Validation error: {e}")
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
        logger.error(f"Login error: {e}")
        return jsonify({'success': False, 'message': f'Login failed: {str(e)}'})

@app.route('/api/admin_login', methods=['POST'])
def admin_login():
    """Admin login with auto-creation if not exists"""
    try:
        data = request.get_json()
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        
        logger.info(f"🔐 Admin login attempt: {username}")
        
        if not username or not password:
            return jsonify({'success': False, 'message': 'Username and password are required'})
        
        # Special case: if trying to login as admin with default password, create if not exists
        if username == "admin" and password == "admin123":
            admin = get_admin("admin")
            if not admin:
                logger.info("⚠️ Admin account not found, creating on-demand...")
                admin_data = {
                    "username": "admin",
                    "password": "admin123",
                    "role": "admin",
                    "full_name": "System Administrator",
                    "created_at": datetime.now()
                }
                create_admin(admin_data)
                admin = admin_data
                logger.info("✅ Admin account created on-demand")
            
            if admin.get('role') == 'admin' and admin.get('password') == password:
                token = f"admin_token_{username}_{datetime.now().timestamp()}"
                logger.info(f"✅ Admin login successful: {username}")
                
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
            logger.warning(f"❌ Admin not found or invalid role: {username}")
            return jsonify({'success': False, 'message': 'Invalid admin credentials'})
        
        if admin.get('password') != password:
            logger.warning(f"❌ Password mismatch for admin: {username}")
            return jsonify({'success': False, 'message': 'Invalid admin credentials'})
        
        token = f"admin_token_{username}_{datetime.now().timestamp()}"
        logger.info(f"✅ Admin login successful: {username}")
        
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
        logger.error(f"❌ Admin login error: {str(e)}")
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
        
        # Convert ObjectId to string for JSON serialization
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
        logger.error(f"Get user info error: {e}")
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
        logger.error(f"Submit vote error: {e}")
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
        
        return jsonify({
            'success': True,
            'has_voted': user.get('has_voted', False),
            'vote': user_vote
        })
        
    except Exception as e:
        logger.error(f"Get vote history error: {e}")
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
        # Convert ObjectId to string for JSON serialization
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
        logger.error(f"Get results error: {e}")
        return jsonify({'success': False, 'message': f'Failed to get results: {str(e)}'})

@app.route('/api/admin/add_candidate', methods=['POST'])
def admin_add_candidate():
    """Admin: Add a new candidate with image stored in MongoDB"""
    try:
        name = request.form.get('name', '').strip()
        party = request.form.get('party', '').strip()
        
        if not name or not party:
            return jsonify({'success': False, 'message': 'Candidate name and party are required'})
        
        # Generate new candidate ID
        all_candidates = get_all_candidates()
        existing_ids = [c.get('candidate_id', 0) for c in all_candidates]
        new_id = max(existing_ids) + 1 if existing_ids else 1
        
        # Handle image upload - store in MongoDB
        image_url = None
        image_id = None
        if 'image' in request.files:
            file = request.files['image']
            if file and file.filename and allowed_file(file.filename, 'image'):
                # Read file as bytes
                file_bytes = file.read()
                
                # Store image in MongoDB
                image_id = store_image_in_mongodb(file_bytes, 'candidate')
                if image_id:
                    image_url = f'/api/image/{image_id}'
        
        candidate_data = {
            'candidate_id': new_id,
            'name': name,
            'party': party,
            'image_url': image_url,
            'image_id': image_id,
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
        logger.error(f"Error adding candidate: {e}")
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
        
        # Delete the candidate completely
        deleted = delete_candidate(candidate_id)
        
        if deleted:
            # Also delete associated image from MongoDB if exists
            image_id = candidate.get('image_id')
            if image_id and images_collection is not None:
                try:
                    obj_id = safe_object_id(image_id)
                    if obj_id:
                        images_collection.delete_one({'_id': obj_id})
                except Exception as e:
                    logger.error(f"Error deleting candidate image: {e}")
            
            return jsonify({
                'success': True,
                'message': f'Candidate {candidate_name} removed successfully'
            })
        else:
            return jsonify({'success': False, 'message': 'Failed to remove candidate'})
        
    except Exception as e:
        logger.error(f"Error removing candidate: {e}")
        return jsonify({'success': False, 'message': f'Failed to remove candidate: {str(e)}'})

# Error handlers
@app.errorhandler(404)
def not_found(error):
    return jsonify({'success': False, 'message': 'Endpoint not found'}), 404

@app.errorhandler(500)
def server_error(error):
    return jsonify({'success': False, 'message': 'Internal server error'}), 500

# Health check endpoint for Render
@app.route('/health', methods=['GET'])
def health_check():
    """Health check endpoint for Render"""
    return jsonify({'status': 'healthy'}), 200

# Main entry point
if __name__ == '__main__':
    # Only run debug server in development
    if os.environ.get('RENDER') != 'true':
        print("="*60)
        print("ONLINE VOTING SYSTEM WITH AADHAAR XML INTEGRATION")
        print("="*60)
        print(f"📊 MongoDB: {'✅ Connected' if mongodb_connected else '❌ Not Connected'}")
        
        # Initialize admin
        admin_created = init_default_admin()
        
        print("\n🔑 ADMIN LOGIN CREDENTIALS:")
        if admin_created:
            print("   Username: admin")
            print("   Password: admin123")
        else:
            print("   ❌ Admin account could not be created")
        
        print("\n📝 IMPORTANT:")
        print("   - Registration requires Aadhaar XML file upload")
        print("   - Download your Aadhaar XML from: https://tathya.uidai.gov.in/access/login?role=resident")
        print("   - Profile photos are automatically extracted from XML and stored in MongoDB")
        print("   - Candidate images are stored in MongoDB")
        
        # Get port from environment variable (for Render)
        port = int(os.environ.get('PORT', 5000))
        
        print(f"\n🌐 Server URL: http://0.0.0.0:{port}")
        print("="*60)
        
        # Run the Flask app
        app.run(host='0.0.0.0', port=port, debug=False)
