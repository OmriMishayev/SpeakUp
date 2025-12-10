import os
import uuid
import random
from datetime import datetime, timedelta
from werkzeug.middleware.proxy_fix import ProxyFix 
from werkzeug.utils import secure_filename
from flask import Flask, render_template_string, request, send_from_directory, jsonify, url_for, redirect
from flask_socketio import SocketIO, emit, join_room
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from authlib.integrations.flask_client import OAuth
import google.generativeai as genai

app = Flask(__name__)
app.config['SECRET_KEY'] = 'speakup_secret_key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['MAX_CONTENT_LENGTH'] = 16 * 1024 * 1024 

if not os.path.exists(app.config['UPLOAD_FOLDER']):
    os.makedirs(app.config['UPLOAD_FOLDER'])

# תיקון קריטי לשרתים בענן
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

# --- מפתחות גוגל (הנכונים מהקובץ שלך) ---
GOOGLE_CLIENT_ID = "276255877380-037ojacjsbll0kpa5ptgr2dap4bvkenf.apps.googleusercontent.com"
GOOGLE_CLIENT_SECRET = "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"

oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    access_token_url='https://accounts.google.com/o/oauth2/token',
    authorize_url='https://accounts.google.com/o/oauth2/auth',
    api_base_url='https://www.googleapis.com/oauth2/v1/',
    userinfo_endpoint='https://openidconnect.googleapis.com/v1/userinfo',
    client_kwargs={'scope': 'email profile'},
)

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*")
login_manager = LoginManager()
login_manager.init_app(app)

# AI Setup
GOOGLE_API_KEY = "AIzaSyAt5EIux3EauqPvQCHNatMGhdRynu5g2vY"
genai.configure(api_key=GOOGLE_API_KEY)
try: model = genai.GenerativeModel('gemini-2.0-flash')
except: model = genai.GenerativeModel('gemini-pro')

# --- טבלאות ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    nickname = db.Column(db.String(50), nullable=False)
    password = db.Column(db.String(100), nullable=True)
    
    # פרופיל
    image_file = db.Column(db.String(100), default='default.png')
    gender = db.Column(db.String(20))
    city = db.Column(db.String(50))
    age = db.Column(db.Integer)
    bio = db.Column(db.String(200))
    
    is_setup_done = db.Column(db.Boolean, default=False)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender = db.Column(db.String(50), nullable=False)
    room = db.Column(db.String(50), nullable=False)
    content = db.Column(db.String(500), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

@login_manager.user_loader
def load_user(user_id): return User.query.get(int(user_id))

with app.app_context(): db.create_all()

# --- AI Logic ---
def check_message_with_ai(text):
    try:
        response = model.generate_content(f"Analyze: [{text}]. Reply ONLY: SAFE, SUICIDE, or PREDATOR.")
        res = response.text.strip().upper()
        if "SUICIDE" in res: return {"safe": False, "reason": "harm", "alert": "זיהינו מצוקה. כפתור תמיכה זמין."}
        if "PREDATOR" in res: return {"safe": False, "reason": "predator", "alert": "נחסם."}
        return {"safe": True, "reason": "ok", "alert": None}
    except: return {"safe": True, "reason": "error", "alert": None}

# --- Routes ---
@app.route('/')
def index():
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        with open(os.path.join(base_dir, 'SPEAKUP1.html'), 'r', encoding='utf-8') as f:
            return render_template_string(f.read())
    except: return "Error: SPEAKUP1.html missing"

@app.route('/uploads/<filename>')
def uploaded_file(filename): return send_from_directory(app.config['UPLOAD_FOLDER'], filename)

@app.route('/<path:filename>')
def serve_static(filename): return send_from_directory('.', filename)

@app.route('/register', methods=['POST'])
def register():
    data = request.json
    if User.query.filter_by(email=data['email']).first(): return jsonify({'success': False, 'message': 'המייל תפוס'})
    user = User(email=data['email'], nickname="User", password=data['password'])
    db.session.add(user)
    db.session.commit()
    login_user(user)
    return jsonify({'success': True, 'needs_setup': True})

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    user = User.query.filter_by(email=data['email']).first()
    if not user or user.password != data['password']: return jsonify({'success': False, 'message': 'פרטים שגויים'})
    
    login_user(user)
    return jsonify({'success': True, 'username': user.nickname, 'image': user.image_file, 'needs_setup': not user.is_setup_done})

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return jsonify({'success': True})

@app.route('/api/update_profile', methods=['POST'])
@login_required
def update_profile():
    user = current_user
    user.nickname = request.form.get('nickname')
    user.age = request.form.get('age')
    user.city = request.form.get('city')
    user.gender = request.form.get('gender')
    user.bio = request.form.get('bio')
    
    if 'image' in request.files:
        file = request.files['image']
        if file.filename != '':
            filename = secure_filename(f"{user.id}_{uuid.uuid4().hex[:6]}.png")
            file.save(os.path.join(app.config['UPLOAD_FOLDER'], filename))
            user.image_file = filename
            
    user.is_setup_done = True
    db.session.commit()
    return jsonify({'success': True, 'image': user.image_file})

@app.route('/api/history')
@login_required
def get_history():
    room = request.args.get('room')
    msgs = Message.query.filter_by(room=room).order_by(Message.timestamp).all()
    history = []
    for m in msgs:
        local_time = m.timestamp
        today = datetime.utcnow().date()
        if local_time.date() == today: date_str = "היום"
        elif local_time.date() == today - timedelta(days=1): date_str = "אתמול"
        else: date_str = local_time.strftime('%d/%m/%Y')
        
        history.append({
            'user': m.sender, 'msg': m.content, 
            'time': local_time.strftime('%H:%M'), 
            'date_group': date_str
        })
    return jsonify(history)

# --- כאן נמצא התיקון לגוגל לוגין! ---
@app.route('/login/google')
def google_login():
    # בדיקה חכמה: אם אנחנו ב-localhost תשתמש ב-http, אם בענן תשתמש ב-https
    scheme = 'http' if app.debug else 'https'
    redirect_uri = url_for('authorize', _external=True, _scheme=scheme)
    return google.authorize_redirect(redirect_uri)

@app.route('/authorize')
def authorize():
    token = google.authorize_access_token()
    user_info = google.get('userinfo').json()
    
    # וידוא שיש אימייל
    email = user_info.get('email')
    name = user_info.get('name', 'Google User')
    
    if not email:
        return "Error: Could not get email from Google", 400

    user = User.query.filter_by(email=email).first()
    if not user:
        user = User(email=email, nickname=name, password='', is_setup_done=False)
        db.session.add(user)
        db.session.commit()
    
    login_user(user)
    return redirect('/?login_success=true')

# --- סוקטים ---
@socketio.on('join')
def handle_join(data): join_room(data['room'])

@socketio.on('send_message')
def handle_message(data):
    safety = check_message_with_ai(data['message'])
    if not safety['safe']:
        if safety['reason'] == 'harm': emit('warning_popup', {'text': safety['alert']}, to=request.sid)
    else:
        msg = Message(sender=data['username'], room=data['room'], content=data['message'])
        db.session.add(msg)
        db.session.commit()
        emit('receive_message', {'msg': data['message'], 'user': data['username'], 'time': datetime.now().strftime('%H:%M')}, room=data['room'])

# --- נתיב בדיקת חיבור (פותר את הבעיה!) ---
@app.route('/api/current_user')
def get_current_user():
    if current_user.is_authenticated:
        # אם המשתמש מחובר בשרת - נחזיר את הפרטים שלו לדפדפן
        return jsonify({
            'is_logged_in': True,
            'username': current_user.nickname,
            'image': current_user.image_file,
            'is_anonymous': getattr(current_user, 'is_anonymous', False), # שימוש בטוח למקרה שהשדה חסר
            'anon_nick': getattr(current_user, 'anonymous_nickname', None)
        })
    else:
        return jsonify({'is_logged_in': False})
if __name__ == '__main__':
    socketio.run(app, debug=True, allow_unsafe_werkzeug=True)
