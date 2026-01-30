from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from pymongo import MongoClient
from werkzeug.security import generate_password_hash, check_password_hash
import os
import json
import base64
import xml.etree.ElementTree as ET
import zlib
import re
from werkzeug.utils import secure_filename
from datetime import datetime
import tempfile
import numpy as np
from bson import ObjectId

app = Flask(__name__, static_folder='.', static_url_path='')
CORS(app)

# MongoDB Connection
try:
    MONGO_URI = os.environ.get('MONGO_URI', 'mongodb://localhost:27017/')
    client = MongoClient(MONGO_URI, serverSelectionTimeoutMS=5000)
    db = client['online_voting']
    users_collection = db['users']
    votes_collection = db['votes']
    candidates_collection = db['candidates']
    admin_collection = db['admins']
    
    # Create indexes
    users_collection.create_index('username', unique=True)
    users_collection.create_index('aadhaar_number', unique=True, sparse=True)
    
    MONGODB_AVAILABLE = True
    print("✓ MongoDB connected successfully")
except Exception as e:
    MONGODB_AVAILABLE = False
    print(f"✗ MongoDB connection failed: {str(e)}")
    print("Note: Some features will use mock data")

# QR Scanning functionality
try:
    import cv2
    from pyzbar.pyzbar import decode
    QR_SCANNING_AVAILABLE = True
    print("✓ QR scanning libraries loaded")
except ImportError as e:
    QR_SCANNING_AVAILABLE = False
    print(f"✗ QR scanning unavailable: {str(e)}")
    print("Install: pip install opencv-python pyzbar")

# File upload configuration
UPLOAD_FOLDER = 'uploads'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'gif', 'bmp', 'pdf'}
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024  # 16MB max file size

class AadhaarQRDecoder:
    """Enhanced Aadhaar QR Decoder with multiple format support"""
    
    def __init__(self, qr_data: str):
        self.qr_data = qr_data.strip()
        self.decoded_data = {}
    
    def decode(self) -> dict:
        """Main decoding method"""
        print(f"Decoding QR data (length: {len(self.qr_data)})")
        
        try:
            if self._is_xml_format():
                print("Detected: XML format")
                return self._parse_xml_format()
            elif self._is_compressed_format():
                print("Detected: Compressed format (V...)")
                return self._parse_compressed_format()
            elif self._is_plain_text_format():
                print("Detected: Plain text format (pipe separated)")
                return self._parse_plain_text_format()
            else:
                print("Detected: Unknown format, attempting extraction")
                return self._extract_data_from_text()
        except Exception as e:
            print(f"Decoding error: {str(e)}")
            return {'error': f'Decoding failed: {str(e)}', 'raw_data': self.qr_data[:200]}
    
    def _is_xml_format(self) -> bool:
        return self.qr_data.startswith('<?xml') or self.qr_data.startswith('<PrintLetterBarcodeData')
    
    def _is_compressed_format(self) -> bool:
        return self.qr_data.startswith('V') and len(self.qr_data) > 50
    
    def _is_plain_text_format(self) -> bool:
        return '|' in self.qr_data and not self.qr_data.startswith('<?xml')
    
    def _parse_xml_format(self) -> dict:
        try:
            # Ensure XML declaration exists
            xml_data = self.qr_data
            if not xml_data.startswith('<?xml'):
                xml_data = '<?xml version="1.0" encoding="UTF-8"?>' + xml_data
            
            root = ET.fromstring(xml_data)
            data = {}
            
            # Extract all attributes
            for attr_name, attr_value in root.attrib.items():
                data[attr_name.lower()] = attr_value
            
            # Standardize field names
            return self._standardize_fields(data)
        except Exception as e:
            raise ValueError(f"XML parsing error: {str(e)}")
    
    def _parse_compressed_format(self) -> dict:
        try:
            # Remove leading 'V' and decode
            compressed_data = base64.b64decode(self.qr_data[1:])
            
            # Decompress (zlib)
            xml_data = zlib.decompress(compressed_data, 15 + 32).decode('utf-8')
            print(f"Decompressed XML length: {len(xml_data)}")
            
            # Now parse the XML
            self.qr_data = xml_data
            return self._parse_xml_format()
        except Exception as e:
            raise ValueError(f"Decompression error: {str(e)}")
    
    def _parse_plain_text_format(self) -> dict:
        try:
            parts = self.qr_data.split('|')
            print(f"Found {len(parts)} pipe-separated parts")
            
            # Map fields according to Aadhaar QR specification
            field_mapping = [
                'uid', 'name', 'dob', 'gender', 'co', 'house', 
                'street', 'landmark', 'locality', 'vtc', 'dist', 
                'state', 'pc', 'email', 'mobile'
            ]
            
            data = {}
            for i, part in enumerate(parts):
                if i < len(field_mapping) and part:
                    data[field_mapping[i]] = part
            
            # Build full address
            address_parts = []
            for field in ['co', 'house', 'street', 'landmark', 'locality', 'vtc', 'dist', 'state', 'pc']:
                if field in data and data[field]:
                    address_parts.append(data[field])
            
            # Create standardized output
            standardized = {
                'uid': data.get('uid', ''),
                'name': data.get('name', ''),
                'dob': data.get('dob', ''),
                'gender': data.get('gender', ''),
                'address': ', '.join(filter(None, address_parts)),
                'email': data.get('email', ''),
                'mobile': data.get('mobile', ''),
                'raw_format': 'plain_text'
            }
            
            return {k: v for k, v in standardized.items() if v}
        except Exception as e:
            raise ValueError(f"Text parsing error: {str(e)}")
    
    def _extract_data_from_text(self) -> dict:
        """Extract possible Aadhaar information from any text"""
        result = {'raw_data': self.qr_data[:500]}
        
        # Find 12-digit Aadhaar number
        uid_match = re.search(r'\b\d{4}\s?\d{4}\s?\d{4}\b', self.qr_data)
        if uid_match:
            result['uid'] = uid_match.group().replace(' ', '')
        
        # Find name (uppercase words)
        name_match = re.search(r'([A-Z][A-Z\s]+[A-Z])', self.qr_data[:100])
        if name_match:
            result['name'] = name_match.group().strip()
        
        # Find date
        date_patterns = [
            r'\d{2}/\d{2}/\d{4}',
            r'\d{2}-\d{2}-\d{4}',
            r'\d{4}/\d{2}/\d{2}',
            r'\d{4}-\d{2}-\d{2}'
        ]
        for pattern in date_patterns:
            date_match = re.search(pattern, self.qr_data)
            if date_match:
                result['dob'] = date_match.group()
                break
        
        # Find gender
        if re.search(r'\b(M|MALE|F|FEMALE)\b', self.qr_data, re.IGNORECASE):
            gender_match = re.search(r'\b(M|MALE|F|FEMALE)\b', self.qr_data, re.IGNORECASE)
            result['gender'] = gender_match.group().upper()
        
        return result
    
    def _standardize_fields(self, data: dict) -> dict:
        """Standardize field names"""
        mapping = {
            'uid': ['uid', 'aadhaar', 'aadhaarnumber'],
            'name': ['name', 'n'],
            'dob': ['dob', 'dateofbirth', 'yob'],
            'gender': ['gender', 'g', 'sex'],
            'email': ['email', 'e'],
            'mobile': ['mobile', 'phone', 'm', 'contactno']
        }
        
        result = {}
        for std_field, variants in mapping.items():
            for variant in variants:
                if variant in data:
                    result[std_field] = data[variant]
                    break
        
        # Build address
        address_fields = ['co', 'house', 'street', 'loc', 'locality', 'vtc', 
                         'po', 'dist', 'subdist', 'state', 'pc', 'address']
        address_parts = []
        for field in address_fields:
            if field in data and data[field]:
                address_parts.append(data[field])
        
        if address_parts:
            result['address'] = ', '.join(address_parts)
        
        result['raw_format'] = 'xml'
        return result

def scan_qr_from_image_file(file_path: str) -> str:
    """Scan QR code from image file with enhanced detection"""
    if not QR_SCANNING_AVAILABLE:
        print("QR scanning libraries not available")
        return ""
    
    try:
        # Read image
        img = cv2.imread(file_path)
        if img is None:
            print(f"Cannot read image: {file_path}")
            return ""
        
        print(f"Image dimensions: {img.shape}")
        
        # Convert to grayscale
        gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
        
        # Try different preprocessing methods
        methods = [
            ("Original", gray),
            ("Binary", cv2.threshold(gray, 127, 255, cv2.THRESH_BINARY)[1]),
            ("Adaptive Threshold", cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
                                               cv2.THRESH_BINARY, 11, 2)),
            ("Blur + Threshold", cv2.threshold(cv2.GaussianBlur(gray, (5, 5), 0), 127, 255, cv2.THRESH_BINARY)[1])
        ]
        
        for method_name, processed_img in methods:
            print(f"Trying method: {method_name}")
            decoded_objects = decode(processed_img)
            
            if decoded_objects:
                for obj in decoded_objects:
                    if obj.type == 'QRCODE':
                        qr_data = obj.data.decode('utf-8')
                        print(f"✓ Found QR code ({method_name})")
                        print(f"Data length: {len(qr_data)}")
                        return qr_data
        
        # If no QR found, try edge detection
        print("Trying edge detection method")
        edges = cv2.Canny(gray, 100, 200)
        decoded_objects = decode(edges)
        
        if decoded_objects:
            for obj in decoded_objects:
                if obj.type == 'QRCODE':
                    qr_data = obj.data.decode('utf-8')
                    print(f"✓ Found QR code (Edge Detection)")
                    return qr_data
        
        print("No QR code found")
        return ""
        
    except Exception as e:
        print(f"QR scanning error: {str(e)}")
        return ""

def capture_qr_from_webcam():
    """Capture QR code using webcam with live preview capability"""
    if not QR_SCANNING_AVAILABLE:
        return {"success": False, "message": "QR scanning libraries not available"}
    
    try:
        cap = cv2.VideoCapture(0)
        if not cap.isOpened():
            return {"success": False, "message": "Cannot access webcam"}
        
        # Set camera properties for better QR detection
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 1280)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 720)
        cap.set(cv2.CAP_PROP_BRIGHTNESS, 0.5)
        cap.set(cv2.CAP_PROP_CONTRAST, 0.5)
        
        qr_data = ""
        frames_processed = 0
        max_frames = 300  # Process up to 300 frames (30 seconds at 10fps)
        
        while frames_processed < max_frames:
            ret, frame = cap.read()
            if not ret:
                break
            
            # Resize for faster processing
            frame = cv2.resize(frame, (640, 480))
            
            # Convert to grayscale
            gray = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
            
            # Apply adaptive thresholding
            processed = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, 
                                            cv2.THRESH_BINARY, 11, 2)
            
            decoded_objects = decode(processed)
            
            for obj in decoded_objects:
                if obj.type == 'QRCODE':
                    qr_data = obj.data.decode('utf-8')
                    
                    # Draw rectangle around QR code
                    points = obj.polygon
                    if len(points) == 4:
                        pts = np.array(points, np.int32)
                        pts = pts.reshape((-1, 1, 2))
                        cv2.polylines(frame, [pts], True, (0, 255, 0), 3)
                    
                    # Add text
                    cv2.putText(frame, "QR Code Detected!", (50, 50), 
                              cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 255, 0), 2)
                    
                    # Show frame for a moment
                    cv2.imshow('QR Scanner - Press Q to exit', frame)
                    cv2.waitKey(1000)
                    
                    cap.release()
                    cv2.destroyAllWindows()
                    return {"success": True, "qr_data": qr_data}
            
            # Show live feed
            cv2.imshow('QR Scanner - Press Q to exit', frame)
            
            # Press 'q' to quit early
            if cv2.waitKey(1) & 0xFF == ord('q'):
                break
            
            frames_processed += 1
        
        cap.release()
        cv2.destroyAllWindows()
        return {"success": False, "message": "No QR code detected"}
        
    except Exception as e:
        if 'cap' in locals():
            cap.release()
        cv2.destroyAllWindows()
        return {"success": False, "message": f"Webcam error: {str(e)}"}

def allowed_file(filename):
    """Check if file extension is allowed"""
    return '.' in filename and \
           filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

def create_default_admin():
    """Create default admin user"""
    try:
        admin_exists = admin_collection.find_one({'username': 'admin'})
        if not admin_exists:
            hashed_password = generate_password_hash('admin@123')
            admin_collection.insert_one({
                'username': 'admin',
                'password': hashed_password,
                'role': 'superadmin',
                'created_at': datetime.now()
            })
            print("✓ Default admin user created")
    except Exception as e:
        print(f"✗ Failed to create admin: {str(e)}")

def create_sample_candidates():
    """Create sample candidates"""
    try:
        candidates_exist = candidates_collection.count_documents({})
        if candidates_exist == 0:
            candidates = [
                {'candidate_id': 1, 'name': 'John Smith', 'party': 'National Party', 'is_active': True},
                {'candidate_id': 2, 'name': 'Emma Johnson', 'party': 'Progressive Alliance', 'is_active': True},
                {'candidate_id': 3, 'name': 'Michael Brown', 'party': 'Unity Front', 'is_active': True}
            ]
            candidates_collection.insert_many(candidates)
            print("✓ Sample candidates created")
    except Exception as e:
        print(f"✗ Failed to create candidates: {str(e)}")

# Initialize database
if MONGODB_AVAILABLE:
    create_default_admin()
    create_sample_candidates()

# ========== User Authentication Routes ==========

@app.route('/api/validate_aadhaar', methods=['POST'])
def validate_aadhaar():
    """Validate Aadhaar QR and check for duplicates"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'No data provided'}), 400
        
        qr_data = data.get('qr_data', '').strip()
        
        if not qr_data:
            return jsonify({'success': False, 'message': 'QR data is required'}), 400
        
        # Decode QR data
        decoder = AadhaarQRDecoder(qr_data)
        decoded_data = decoder.decode()
        
        # Extract Aadhaar number
        aadhaar_number = decoded_data.get('uid')
        
        if not aadhaar_number:
            return jsonify({
                'success': False,
                'message': 'No Aadhaar number found in QR code',
                'data': decoded_data
            }), 400
        
        # Validate Aadhaar format
        if len(aadhaar_number) != 12 or not aadhaar_number.isdigit():
            return jsonify({
                'success': False,
                'message': 'Invalid Aadhaar number format',
                'aadhaar': aadhaar_number
            }), 400
        
        # Check if Aadhaar already registered
        is_duplicate = False
        existing_user = None
        
        if MONGODB_AVAILABLE:
            existing_user = users_collection.find_one({'aadhaar_number': aadhaar_number})
            if existing_user:
                is_duplicate = True
        
        return jsonify({
            'success': True,
            'message': 'Aadhaar validated successfully',
            'data': decoded_data,
            'aadhaar_number': aadhaar_number,
            'is_duplicate': is_duplicate,
            'existing_user': existing_user['username'] if existing_user else None
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Validation failed: {str(e)}'}), 500

@app.route('/api/scan_uploaded_image', methods=['POST'])
def scan_uploaded_image():
    """Scan QR code from uploaded image file"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'message': 'No file provided'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'message': 'No file selected'}), 400
        
        if not allowed_file(file.filename):
            return jsonify({
                'success': False, 
                'message': f'File type not allowed. Allowed types: {", ".join(ALLOWED_EXTENSIONS)}'
            }), 400
        
        # Save temporary file
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, secure_filename(file.filename))
        file.save(temp_path)
        
        print(f"Scanning QR from uploaded image: {file.filename}")
        
        # Scan QR code
        qr_data = scan_qr_from_image_file(temp_path)
        
        # Clean up temporary file
        if os.path.exists(temp_path):
            os.remove(temp_path)
        
        if not qr_data:
            return jsonify({'success': False, 'message': 'No QR code found in image'}), 400
        
        # Decode QR data
        decoder = AadhaarQRDecoder(qr_data)
        decoded_data = decoder.decode()
        
        # Extract Aadhaar number
        aadhaar_number = decoded_data.get('uid')
        
        if not aadhaar_number:
            return jsonify({
                'success': False,
                'message': 'No Aadhaar number found in QR code',
                'data': decoded_data
            }), 400
        
        # Check if Aadhaar already registered
        is_duplicate = False
        existing_user = None
        
        if MONGODB_AVAILABLE:
            existing_user = users_collection.find_one({'aadhaar_number': aadhaar_number})
            if existing_user:
                is_duplicate = True
        
        return jsonify({
            'success': True,
            'message': 'QR code scanned successfully',
            'qr_data': qr_data,
            'data': decoded_data,
            'aadhaar_number': aadhaar_number,
            'is_duplicate': is_duplicate,
            'existing_user': existing_user['username'] if existing_user else None
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Image scanning failed: {str(e)}'}), 500

@app.route('/api/register', methods=['POST'])
def register():
    """User registration with Aadhaar validation"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'No data provided'}), 400
        
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        full_name = data.get('fullName', '').strip() or username
        qr_data = data.get('qr_data', '').strip()
        
        if not username or not password:
            return jsonify({'success': False, 'message': 'Username and password are required'}), 400
        
        if len(username) < 3:
            return jsonify({'success': False, 'message': 'Username must be at least 3 characters'}), 400
        
        if len(password) < 6:
            return jsonify({'success': False, 'message': 'Password must be at least 6 characters'}), 400
        
        # Validate Aadhaar QR is required
        if not qr_data:
            return jsonify({
                'success': False, 
                'message': 'Aadhaar QR code is required for registration'
            }), 400
        
        # Decode QR data to extract Aadhaar number
        decoder = AadhaarQRDecoder(qr_data)
        decoded_data = decoder.decode()
        aadhaar_number = decoded_data.get('uid')
        
        if not aadhaar_number or len(aadhaar_number) != 12 or not aadhaar_number.isdigit():
            return jsonify({
                'success': False, 
                'message': 'Invalid Aadhaar QR code. Please scan a valid Aadhaar QR.'
            }), 400
        
        if MONGODB_AVAILABLE:
            # Check if username already exists
            existing_user = users_collection.find_one({'username': username})
            if existing_user:
                return jsonify({'success': False, 'message': 'Username already exists'}), 400
            
            # Check if Aadhaar already registered
            existing_aadhaar = users_collection.find_one({'aadhaar_number': aadhaar_number})
            if existing_aadhaar:
                return jsonify({
                    'success': False, 
                    'message': f'Aadhaar number {aadhaar_number} is already registered'
                }), 400
            
            # Create new user
            hashed_password = generate_password_hash(password)
            user_data = {
                'username': username,
                'password': hashed_password,
                'full_name': full_name,
                'aadhaar_number': aadhaar_number,
                'qr_data': decoded_data,
                'is_verified': True,
                'created_at': datetime.now(),
                'last_login': None
            }
            
            users_collection.insert_one(user_data)
        
        return jsonify({
            'success': True,
            'message': 'Registration successful with Aadhaar verification',
            'user': {
                'username': username,
                'full_name': full_name,
                'aadhaar_number': aadhaar_number,
                'is_verified': True
            }
        })
        
    except Exception as e:
        if 'duplicate key error' in str(e).lower() and 'aadhaar_number' in str(e):
            return jsonify({
                'success': False,
                'message': 'This Aadhaar number is already registered'
            }), 400
        return jsonify({'success': False, 'message': f'Registration failed: {str(e)}'}), 500

@app.route('/api/login', methods=['POST'])
def login():
    """User login"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'No data provided'}), 400
        
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        
        if not username or not password:
            return jsonify({'success': False, 'message': 'Username and password are required'}), 400
        
        if MONGODB_AVAILABLE:
            user = users_collection.find_one({'username': username})
            if not user:
                return jsonify({'success': False, 'message': 'User does not exist'}), 401
            
            if not check_password_hash(user['password'], password):
                return jsonify({'success': False, 'message': 'Incorrect password'}), 401
            
            # Update last login time
            users_collection.update_one(
                {'_id': user['_id']},
                {'$set': {'last_login': datetime.now()}}
            )
            
            user_data = {
                'username': user['username'],
                'full_name': user.get('full_name', user['username']),
                'aadhaar_number': user.get('aadhaar_number'),
                'is_verified': user.get('is_verified', False)
            }
        else:
            # Mock data (for testing)
            user_data = {
                'username': username,
                'full_name': username,
                'aadhaar_number': '999999999999',
                'is_verified': True
            }
        
        return jsonify({
            'success': True,
            'message': 'Login successful',
            'user': user_data
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Login failed: {str(e)}'}), 500

@app.route('/api/upload_qr', methods=['POST'])
def upload_qr():
    """Upload QR image and process"""
    try:
        username = request.form.get('username')
        if not username:
            return jsonify({'success': False, 'message': 'Please login first'}), 401
        
        if 'qr_image' not in request.files:
            return jsonify({'success': False, 'message': 'No QR image file provided'}), 400
        
        file = request.files['qr_image']
        if file.filename == '':
            return jsonify({'success': False, 'message': 'No file selected'}), 400
        
        if not allowed_file(file.filename):
            return jsonify({
                'success': False, 
                'message': f'File type not allowed. Allowed types: {", ".join(ALLOWED_EXTENSIONS)}'
            }), 400
        
        # Save temporary file
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, secure_filename(file.filename))
        file.save(temp_path)
        
        print(f"Processing QR image for user {username}: {file.filename}")
        
        # Scan QR code
        qr_data = scan_qr_from_image_file(temp_path)
        
        if not qr_data:
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return jsonify({'success': False, 'message': 'No QR code found in image'}), 400
        
        # Decode QR data
        decoder = AadhaarQRDecoder(qr_data)
        decoded_data = decoder.decode()
        
        # Check if this Aadhaar belongs to another user
        aadhaar_number = decoded_data.get('uid')
        if aadhaar_number and MONGODB_AVAILABLE:
            existing_user = users_collection.find_one({'aadhaar_number': aadhaar_number})
            if existing_user and existing_user['username'] != username:
                if os.path.exists(temp_path):
                    os.remove(temp_path)
                return jsonify({
                    'success': False,
                    'message': f'This Aadhaar is already registered to user: {existing_user["username"]}'
                }), 400
        
        # Save to database
        if MONGODB_AVAILABLE:
            update_data = {
                'qr_data': decoded_data,
                'qr_updated': datetime.now()
            }
            if aadhaar_number:
                update_data['aadhaar_number'] = aadhaar_number
                update_data['is_verified'] = True
            
            users_collection.update_one(
                {'username': username},
                {'$set': update_data}
            )
        
        # Save the uploaded file permanently
        if aadhaar_number:
            # Save with Aadhaar number as filename
            filename = f"{aadhaar_number}_{datetime.now().strftime('%Y%m%d_%H%M%S')}.{file.filename.split('.')[-1]}"
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.seek(0)  # Reset file pointer
            file.save(file_path)
            print(f"Saved uploaded file: {file_path}")
        
        # Clean up temporary file
        if os.path.exists(temp_path):
            os.remove(temp_path)
        
        return jsonify({
            'success': True,
            'message': 'QR image processed successfully',
            'filename': file.filename,
            'qr_data_length': len(qr_data),
            'data': decoded_data
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Processing failed: {str(e)}'}), 500

@app.route('/api/upload_qr_text', methods=['POST'])
def upload_qr_text():
    """Upload QR text and process"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'No data provided'}), 400
        
        username = data.get('username')
        qr_text = data.get('qr_text', '').strip()
        
        if not username:
            return jsonify({'success': False, 'message': 'Please login first'}), 401
        
        if not qr_text:
            return jsonify({'success': False, 'message': 'QR text is empty'}), 400
        
        print(f"Processing QR text for user {username}")
        
        # Decode QR data
        decoder = AadhaarQRDecoder(qr_text)
        decoded_data = decoder.decode()
        
        # Check if this Aadhaar belongs to another user
        aadhaar_number = decoded_data.get('uid')
        if aadhaar_number and MONGODB_AVAILABLE:
            existing_user = users_collection.find_one({'aadhaar_number': aadhaar_number})
            if existing_user and existing_user['username'] != username:
                return jsonify({
                    'success': False,
                    'message': f'This Aadhaar is already registered to user: {existing_user["username"]}'
                }), 400
        
        # Save to database
        if MONGODB_AVAILABLE:
            update_data = {
                'qr_data': decoded_data,
                'qr_updated': datetime.now()
            }
            if aadhaar_number:
                update_data['aadhaar_number'] = aadhaar_number
                update_data['is_verified'] = True
            
            users_collection.update_one(
                {'username': username},
                {'$set': update_data}
            )
        
        return jsonify({
            'success': True,
            'message': 'QR text processed successfully',
            'data': decoded_data
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Processing failed: {str(e)}'}), 500

@app.route('/api/get_qr_info', methods=['POST'])
def get_qr_info():
    """Get user's QR information"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'No data provided'}), 400
        
        username = data.get('username')
        if not username:
            return jsonify({'success': False, 'message': 'Please login first'}), 401
        
        if MONGODB_AVAILABLE:
            user = users_collection.find_one({'username': username})
            if user and 'qr_data' in user:
                return jsonify({
                    'success': True,
                    'message': 'QR data found',
                    'data': user['qr_data'],
                    'aadhaar_number': user.get('aadhaar_number'),
                    'is_verified': user.get('is_verified', False)
                })
            else:
                return jsonify({
                    'success': False,
                    'message': 'No QR data found'
                })
        else:
            # Mock data
            return jsonify({
                'success': True,
                'message': 'Mock QR data',
                'data': {
                    'name': 'John Doe',
                    'uid': '999999999999',
                    'dob': '01/01/1990',
                    'gender': 'M',
                    'email': 'test@example.com'
                },
                'aadhaar_number': '999999999999',
                'is_verified': True
            })
            
    except Exception as e:
        return jsonify({'success': False, 'message': f'Failed to get QR info: {str(e)}'}), 500

# ========== Voting System Routes ==========

@app.route('/api/admin_login', methods=['POST'])
def admin_login():
    """Admin login"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'No data provided'}), 400
        
        username = data.get('username', '').strip()
        password = data.get('password', '').strip()
        
        if not username or not password:
            return jsonify({'success': False, 'message': 'Username and password are required'}), 400
        
        if MONGODB_AVAILABLE:
            admin = admin_collection.find_one({'username': username})
            if not admin:
                return jsonify({'success': False, 'message': 'Admin user does not exist'}), 401
            
            if not check_password_hash(admin['password'], password):
                return jsonify({'success': False, 'message': 'Incorrect password'}), 401
            
            # Update last login time
            admin_collection.update_one(
                {'_id': admin['_id']},
                {'$set': {'last_login': datetime.now()}}
            )
            
            admin_data = {
                'username': admin['username'],
                'role': admin.get('role', 'admin'),
                'last_login': admin.get('last_login')
            }
        else:
            # Mock data
            admin_data = {
                'username': 'admin',
                'role': 'superadmin',
                'last_login': datetime.now().isoformat()
            }
        
        return jsonify({
            'success': True,
            'message': 'Admin login successful',
            'admin': admin_data
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'Admin login failed: {str(e)}'}), 500

@app.route('/api/get_user_info', methods=['POST'])
def get_user_info():
    """Get user information for voting page"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'No data provided'}), 400
        
        username = data.get('username')
        if not username:
            return jsonify({'success': False, 'message': 'Username required'}), 400
        
        if MONGODB_AVAILABLE:
            user = users_collection.find_one({'username': username})
            if not user:
                return jsonify({'success': False, 'message': 'User not found'}), 404
            
            # Check if user has already voted
            has_voted = votes_collection.find_one({'username': username}) is not None
            
            # Get candidate list
            candidates = list(candidates_collection.find({'is_active': True}, {'_id': 0, 'candidate_id': 1, 'name': 1, 'party': 1}))
            
            user_data = {
                'username': user['username'],
                'full_name': user.get('full_name', user['username']),
                'aadhaar_number': user.get('aadhaar_number'),
                'is_verified': user.get('is_verified', False),
                'has_voted': has_voted,
                'qr_data': user.get('qr_data', {})
            }
            
            return jsonify({
                'success': True,
                'message': 'User info retrieved',
                'user': user_data,
                'candidates': candidates
            })
        else:
            # Mock data
            return jsonify({
                'success': True,
                'message': 'Mock user info',
                'user': {
                    'username': username,
                    'full_name': username,
                    'aadhaar_number': '999999999999',
                    'is_verified': True,
                    'has_voted': False,
                    'qr_data': {'name': username}
                },
                'candidates': [
                    {'candidate_id': 1, 'name': 'John Smith', 'party': 'National Party'},
                    {'candidate_id': 2, 'name': 'Emma Johnson', 'party': 'Progressive Alliance'},
                    {'candidate_id': 3, 'name': 'Michael Brown', 'party': 'Unity Front'}
                ]
            })
            
    except Exception as e:
        return jsonify({'success': False, 'message': f'Failed to get user info: {str(e)}'}), 500

@app.route('/api/submit_vote', methods=['POST'])
def submit_vote():
    """Submit a vote"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'No data provided'}), 400
        
        username = data.get('username')
        candidate_id = data.get('candidate_id')
        
        if not username or not candidate_id:
            return jsonify({'success': False, 'message': 'Username and candidate ID required'}), 400
        
        if MONGODB_AVAILABLE:
            # Check if user exists
            user = users_collection.find_one({'username': username})
            if not user:
                return jsonify({'success': False, 'message': 'User not found'}), 404
            
            # Check if user is verified
            if not user.get('is_verified', False):
                return jsonify({'success': False, 'message': 'User not verified. Please verify Aadhaar first.'}), 400
            
            # Check if user has already voted
            existing_vote = votes_collection.find_one({'username': username})
            if existing_vote:
                return jsonify({'success': False, 'message': 'You have already voted'}), 400
            
            # Check if candidate exists
            candidate = candidates_collection.find_one({'candidate_id': candidate_id, 'is_active': True})
            if not candidate:
                return jsonify({'success': False, 'message': 'Invalid candidate'}), 400
            
            # Record the vote
            vote_data = {
                'username': username,
                'aadhaar_number': user.get('aadhaar_number'),
                'candidate_id': candidate_id,
                'candidate_name': candidate.get('name'),
                'candidate_party': candidate.get('party'),
                'voted_at': datetime.now(),
                'ip_address': request.remote_addr
            }
            
            votes_collection.insert_one(vote_data)
            
            return jsonify({
                'success': True,
                'message': 'Vote submitted successfully',
                'vote': {
                    'candidate': candidate.get('name'),
                    'party': candidate.get('party'),
                    'timestamp': vote_data['voted_at'].isoformat()
                }
            })
        else:
            return jsonify({
                'success': True,
                'message': 'Mock vote submitted',
                'vote': {
                    'candidate': 'Mock Candidate',
                    'party': 'Mock Party',
                    'timestamp': datetime.now().isoformat()
                }
            })
            
    except Exception as e:
        return jsonify({'success': False, 'message': f'Failed to submit vote: {str(e)}'}), 500

@app.route('/api/admin/get_results', methods=['GET'])
def get_voting_results():
    """Get voting results (admin only)"""
    try:
        # Add authentication check here in production
        if MONGODB_AVAILABLE:
            # Get total votes per candidate
            pipeline = [
                {
                    '$group': {
                        '_id': '$candidate_id',
                        'candidate_name': {'$first': '$candidate_name'},
                        'candidate_party': {'$first': '$candidate_party'},
                        'total_votes': {'$sum': 1}
                    }
                },
                {'$sort': {'total_votes': -1}}
            ]
            
            results = list(votes_collection.aggregate(pipeline))
            
            # Get total votes
            total_votes = votes_collection.count_documents({})
            
            # Get voter statistics
            total_users = users_collection.count_documents({'is_verified': True})
            voted_users = votes_collection.distinct('username')
            voting_percentage = (len(voted_users) / total_users * 100) if total_users > 0 else 0
            
            # Get total candidates
            total_candidates = candidates_collection.count_documents({'is_active': True})
            
            return jsonify({
                'success': True,
                'results': results,
                'statistics': {
                    'total_votes': total_votes,
                    'total_voters': total_users,
                    'voted_users': len(voted_users),
                    'voting_percentage': round(voting_percentage, 2),
                    'total_candidates': total_candidates
                }
            })
        else:
            # Mock results
            return jsonify({
                'success': True,
                'results': [
                    {'candidate_name': 'John Smith', 'candidate_party': 'National Party', 'total_votes': 150},
                    {'candidate_name': 'Emma Johnson', 'candidate_party': 'Progressive Alliance', 'total_votes': 120},
                    {'candidate_name': 'Michael Brown', 'candidate_party': 'Unity Front', 'total_votes': 80}
                ],
                'statistics': {
                    'total_votes': 350,
                    'total_voters': 500,
                    'voted_users': 350,
                    'voting_percentage': 70.0,
                    'total_candidates': 3
                }
            })
            
    except Exception as e:
        return jsonify({'success': False, 'message': f'Failed to get results: {str(e)}'}), 500

@app.route('/api/admin/add_candidate', methods=['POST'])
def add_candidate():
    """Add candidate (admin only)"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'No data provided'}), 400
        
        name = data.get('name', '').strip()
        party = data.get('party', '').strip()
        
        if not name or not party:
            return jsonify({'success': False, 'message': 'Candidate name and party required'}), 400
        
        if MONGODB_AVAILABLE:
            # Generate candidate ID
            last_candidate = candidates_collection.find_one(sort=[("candidate_id", -1)])
            candidate_id = last_candidate['candidate_id'] + 1 if last_candidate else 1
            
            candidate_data = {
                'candidate_id': candidate_id,
                'name': name,
                'party': party,
                'added_at': datetime.now(),
                'is_active': True
            }
            
            candidates_collection.insert_one(candidate_data)
            
            return jsonify({
                'success': True,
                'message': f'Candidate {name} added successfully',
                'candidate': {
                    'candidate_id': candidate_id,
                    'name': name,
                    'party': party
                }
            })
        else:
            return jsonify({
                'success': True,
                'message': 'Mock candidate added',
                'candidate': {
                    'candidate_id': 99,
                    'name': name,
                    'party': party
                }
            })
            
    except Exception as e:
        return jsonify({'success': False, 'message': f'Failed to add candidate: {str(e)}'}), 500

@app.route('/api/get_vote_history', methods=['POST'])
def get_vote_history():
    """Get user's vote history"""
    try:
        data = request.get_json()
        if not data:
            return jsonify({'success': False, 'message': 'No data provided'}), 400
        
        username = data.get('username')
        if not username:
            return jsonify({'success': False, 'message': 'Username required'}), 400
        
        if MONGODB_AVAILABLE:
            vote = votes_collection.find_one({'username': username})
            if vote:
                return jsonify({
                    'success': True,
                    'has_voted': True,
                    'vote': {
                        'candidate_name': vote.get('candidate_name'),
                        'candidate_party': vote.get('candidate_party'),
                        'voted_at': vote.get('voted_at').isoformat() if vote.get('voted_at') else None
                    }
                })
            else:
                return jsonify({
                    'success': True,
                    'has_voted': False,
                    'vote': None
                })
        else:
            return jsonify({
                'success': True,
                'has_voted': True,
                'vote': {
                    'candidate_name': 'John Smith',
                    'candidate_party': 'National Party',
                    'voted_at': datetime.now().isoformat()
                }
            })
            
    except Exception as e:
        return jsonify({'success': False, 'message': f'Failed to get vote history: {str(e)}'}), 500

# ========== Original Flask Routes ==========

@app.route('/')
def index():
    """Main page"""
    return send_from_directory('.', 'index.html')

@app.route('/voting')
def voting_page():
    """Voting page"""
    return send_from_directory('.', 'voting.html')

@app.route('/admin')
def admin_page():
    """Admin page"""
    return send_from_directory('.', 'admin.html')

@app.route('/api/decode', methods=['POST'])
def decode_qr():
    """Decode QR data API"""
    try:
        data = request.get_json()
        if not data or 'qr_data' not in data:
            return jsonify({'success': False, 'message': 'No QR data provided'}), 400
        
        qr_data = data['qr_data'].strip()
        if not qr_data:
            return jsonify({'success': False, 'message': 'QR data is empty'}), 400
        
        decoder = AadhaarQRDecoder(qr_data)
        result = decoder.decode()
        
        return jsonify({
            'success': True,
            'message': 'Decoding successful',
            'data': result
        })
    except Exception as e:
        return jsonify({'success': False, 'message': f'Decoding failed: {str(e)}'}), 500

@app.route('/api/upload', methods=['POST'])
def upload_file():
    """Upload and process QR image"""
    try:
        if 'file' not in request.files:
            return jsonify({'success': False, 'message': 'No file provided'}), 400
        
        file = request.files['file']
        if file.filename == '':
            return jsonify({'success': False, 'message': 'No file selected'}), 400
        
        if not allowed_file(file.filename):
            return jsonify({
                'success': False, 
                'message': f'File type not allowed. Allowed types: {", ".join(ALLOWED_EXTENSIONS)}'
            }), 400
        
        # Save temporary file
        temp_dir = tempfile.gettempdir()
        temp_path = os.path.join(temp_dir, secure_filename(file.filename))
        file.save(temp_path)
        
        print(f"Saved temporary file: {temp_path}")
        
        # Scan QR code
        qr_data = scan_qr_from_image_file(temp_path)
        
        if not qr_data:
            # Clean up temporary file
            if os.path.exists(temp_path):
                os.remove(temp_path)
            return jsonify({'success': False, 'message': 'No QR code found in image'}), 400
        
        # Decode QR data
        decoder = AadhaarQRDecoder(qr_data)
        result = decoder.decode()
        
        # Clean up temporary file
        if os.path.exists(temp_path):
            os.remove(temp_path)
        
        return jsonify({
            'success': True,
            'message': 'File processed successfully',
            'qr_data': qr_data[:500] + ('...' if len(qr_data) > 500 else ''),
            'data': result
        })
        
    except Exception as e:
        return jsonify({'success': False, 'message': f'File processing failed: {str(e)}'}), 500

@app.route('/api/webcam_scan', methods=['GET'])
def webcam_scan():
    """Scan QR code using webcam"""
    result = capture_qr_from_webcam()
    return jsonify(result)

@app.route('/api/test_webcam', methods=['GET'])
def test_webcam():
    """Test if webcam is available"""
    if not QR_SCANNING_AVAILABLE:
        return jsonify({'success': False, 'message': 'QR scanning libraries not available'})
    
    try:
        cap = cv2.VideoCapture(0)
        if cap.isOpened():
            cap.release()
            return jsonify({'success': True, 'message': 'Webcam is available'})
        else:
            return jsonify({'success': False, 'message': 'Webcam not accessible'})
    except Exception as e:
        return jsonify({'success': False, 'message': f'Webcam test failed: {str(e)}'})

@app.route('/api/test', methods=['GET'])
def test_api():
    """Test endpoint"""
    test_data = {
        'status': 'Online',
        'qr_scanning': 'Available' if QR_SCANNING_AVAILABLE else 'Unavailable',
        'mongodb': 'Connected' if MONGODB_AVAILABLE else 'Not connected',
        'webcam': 'Available' if QR_SCANNING_AVAILABLE else 'Unavailable',
        'timestamp': datetime.now().isoformat()
    }
    return jsonify(test_data)

@app.route('/api/health', methods=['GET'])
def health_check():
    """Health check endpoint"""
    return jsonify({'status': 'healthy', 'timestamp': datetime.now().isoformat()})

@app.route('/api/sample', methods=['GET'])
def get_sample_qr():
    """Get sample QR data for testing"""
    samples = {
        'xml_sample': '''<?xml version="1.0" encoding="UTF-8"?>
<PrintLetterBarcodeData uid="999999999999" name="John Doe" gender="M" 
    yob="1990" co="Test Company" house="123" street="Main Street" 
    loc="Downtown" vtc="Test Village" po="Test PO" dist="Test District" 
    state="Test State" pc="123456" dob="01/01/1990" email="test@example.com" 
    mobile="9876543210" />''',
        
        'text_sample': '999999999999|John Doe|01/01/1990|M|Test Company|123|Main Street||Downtown|Test Village|Test District|Test State|123456|test@example.com|9876543210',
        
        'compressed_sample': 'V'  # Compressed format needs actual encoding
    }
    
    return jsonify({
        'success': True,
        'samples': samples,
        'instructions': 'Use /api/decode endpoint to decode these samples'
    })

@app.route('/uploads/<filename>')
def uploaded_file(filename):
    """Serve uploaded files"""
    return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

if __name__ == '__main__':
    print("\n" + "="*60)
    print("Enhanced Aadhaar QR Decoding Server with Voting System")
    print("="*60)
    print(f"MongoDB: {'✓ Connected' if MONGODB_AVAILABLE else '✗ Not connected'}")
    print(f"QR Scanning: {'✓ Available' if QR_SCANNING_AVAILABLE else '✗ Unavailable'}")
    print(f"Webcam: {'✓ Available' if QR_SCANNING_AVAILABLE else '✗ Unavailable'}")
    print(f"Upload Folder: {os.path.abspath(UPLOAD_FOLDER)}")
    print("\nAvailable Endpoints:")
    print("  GET  /                         - Main page")
    print("  GET  /voting                   - Voting page")
    print("  GET  /admin                    - Admin panel")
    print("  POST /api/register             - User registration (with Aadhaar)")
    print("  POST /api/login                - User login")
    print("  POST /api/admin_login          - Admin login")
    print("  POST /api/get_user_info        - Get user info for voting")
    print("  POST /api/submit_vote          - Submit vote")
    print("  GET  /api/admin/get_results    - Get voting results (admin)")
    print("  POST /api/admin/add_candidate  - Add candidate (admin)")
    print("  POST /api/validate_aadhaar     - Validate Aadhaar QR")
    print("  POST /api/scan_uploaded_image  - Scan QR from uploaded image")
    print("  POST /api/decode               - Decode QR text data")
    print("  POST /api/upload               - Upload QR image file")
    print("  POST /api/upload_qr            - Upload QR image (with user auth)")
    print("  POST /api/upload_qr_text       - Upload QR text")
    print("  POST /api/get_qr_info          - Get user QR info")
    print("  GET  /api/webcam_scan          - Scan QR using webcam")
    print("  GET  /api/test_webcam          - Test webcam availability")
    print("  GET  /api/test                 - Test server status")
    print("  GET  /api/health               - Health check")
    print("  GET  /api/sample               - Get sample data")
    print("  GET  /uploads/<filename>       - Access uploaded files")
    print("\nStarting server...")
    print("="*60)
    
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=True, host='0.0.0.0', port=port)