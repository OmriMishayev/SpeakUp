import os
import re
import uuid
import random
from flask import Flask, render_template_string, request, send_from_directory, jsonify, url_for, redirect
from flask_socketio import SocketIO, emit, join_room, leave_room
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
from authlib.integrations.flask_client import OAuth
import google.generativeai as genai
from datetime import datetime
from werkzeug.middleware.proxy_fix import ProxyFix 

app = Flask(__name__)
app.config['SECRET_KEY'] = 'speakup_secret_key'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# תיקון ל-Render (HTTPS)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'

# --- המפתחות מהקובץ שהעלית (הזוג התואם) ---
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

# הגדרת ה-AI
GOOGLE_API_KEY = "AIzaSyAt5EIux3EauqPvQCHNatMGhdRynu5g2vY"
genai.configure(api_key=GOOGLE_API_KEY)
try:
    model = genai.GenerativeModel('gemini-2.0-flash')
except:
    model = genai.GenerativeModel('gemini-pro')

# --- טבלאות ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(120), unique=True, nullable=False)
    nickname = db.Column(db.String(50), nullable=False)
    password = db.Column(db.String(100), nullable=True)
    auth_type = db.Column(db.String(20), default='email')
    avatar = db.Column(db.String(10), default='😊')
    is_admin = db.Column(db.Boolean, default=False) 

class Group(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    name = db.Column(db.String(100), unique=True, nullable=False)
    is_public = db.Column(db.Boolean, default=True)
    creator_id = db.Column(db.Integer, db.ForeignKey('user.id'))
    invite_code = db.Column(db.String(50), unique=True)

class Message(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    sender = db.Column(db.String(50), nullable=False)
    room = db.Column(db.String(50), nullable=False)
    content = db.Column(db.String(500), nullable=False)
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

class BlockedLog(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_nickname = db.Column(db.String(50))
    content = db.Column(db.String(500))
    reason = db.Column(db.String(50))
    timestamp = db.Column(db.DateTime, default=datetime.utcnow)

@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

with app.app_context():
    db.create_all()

# --- AI Logic ---
def check_message_with_ai(text):
    try:
        prompt = f"Analyze this message: [{text}]. Reply ONLY with: SAFE, SUICIDE, or PREDATOR."
        response = model.generate_content(prompt)
        result = response.text.strip().upper()
        if "SUICIDE" in result: return {"safe": False, "reason": "harm", "alert": "זיהינו מצוקה. כפתור תמיכה זמין עבורך."}
        if "PREDATOR" in result: return {"safe": False, "reason": "predator", "alert": "נחסם עקב חשד לתוכן פוגעני."}
        return {"safe": True, "reason": "ok", "alert": None}
    except: return {"safe": True, "reason": "error", "alert": None}

# --- Routes ---
@app.route('/')
def index():
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        html_path = os.path.join(base_dir, 'SPEAKUP1.html')
        with open(html_path, 'r', encoding='utf-8') as f:
            return render_template_string(f.read())
    except FileNotFoundError: return "Error: SPEAKUP1.html missing"

@app.route('/<path:filename>')
def serve_static(filename): return send_from_directory('.', filename)

@app.route('/register', methods=['POST'])
def register():
    data = request.json
    if User.query.filter_by(email=data['email']).first():
        return jsonify({'success': False, 'message': 'המייל תפוס'})
    
    is_first_user = (User.query.count() == 0)
    avatars = ['🐶', '🐱', '🦊', '🐻', '🐼', '🐨', '🐯', '🦁', '🐮', '🐷', '🐸', '🐙']
    new_user = User(email=data['email'], nickname=data['nickname'], password=data['password'], 
                    avatar=random.choice(avatars), is_admin=is_first_user)
    db.session.add(new_user)
    db.session.commit()
    login_user(new_user)
    return jsonify({'success': True, 'username': data['nickname'], 'avatar': new_user.avatar, 'is_admin': is_first_user})

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    user = User.query.filter_by(email=data['email']).first()
    
    if not user: return jsonify({'success': False, 'message': 'לא נמצא משתמש, אנא נסה שנית או צור/י חשבון חדש'})
    if user.password != data['password']: return jsonify({'success': False, 'message': 'סיסמה שגויה'})
    
    login_user(user)
    return jsonify({'success': True, 'username': user.nickname, 'avatar': user.avatar, 'is_admin': user.is_admin})

# --- גוגל ---
@app.route('/login/google')
def google_login():
    redirect_uri = url_for('authorize', _external=True, _scheme='https')
    return google.authorize_redirect(redirect_uri)

@app.route('/authorize')
def authorize():
    token = google.authorize_access_token()
    resp = google.get('userinfo')
    user_info = resp.json()
    email, name = user_info['email'], user_info['name']
    
    user = User.query.filter_by(email=email).first()
    if not user:
        is_first_user = (User.query.count() == 0)
        user = User(email=email, nickname=name, auth_type='google', password='', avatar='👤', is_admin=is_first_user)
        db.session.add(user)
        db.session.commit()
    
    login_user(user)
    return redirect('/')

# --- Groups & Chat ---
@app.route('/api/create_group', methods=['POST'])
@login_required
def create_group():
    data = request.json
    if Group.query.filter_by(name=data['name']).first(): return jsonify({'success': False, 'message': 'שם תפוס'})
    invite_code = str(uuid.uuid4())[:8]
    new_group = Group(name=data['name'], is_public=data.get('is_public', True), creator_id=current_user.id, invite_code=invite_code)
    db.session.add(new_group)
    db.session.commit()
    return jsonify({'success': True, 'group_name': data['name'], 'invite_code': invite_code})

@app.route('/api/search_groups')
def search_groups():
    q = request.args.get('q', '')
    groups = Group.query.filter(Group.name.contains(q), Group.is_public == True).all()
    return jsonify([{'name': g.name} for g in groups])

@app.route('/api/admin_stats')
@login_required
def admin_stats():
    if not current_user.is_admin: return jsonify({'error': 'Unauthorized'}), 403
    logs = BlockedLog.query.order_by(BlockedLog.timestamp.desc()).limit(20).all()
    return jsonify({
        'users': User.query.count(),
        'messages': Message.query.count(),
        'logs': [{'user': l.user_nickname, 'content': l.content, 'reason': l.reason, 'time': l.timestamp.strftime('%H:%M')} for l in logs]
    })

@socketio.on('join')
def handle_join(data):
    join_room(data['room'])
    emit('system_message', {'msg': f"{data['username']} הצטרף/ה."}, room=data['room'])

@socketio.on('typing')
def handle_typing(data):
    emit('display_typing', {'user': data['username']}, room=data['room'], include_self=False)

@socketio.on('send_message')
def handle_message(data):
    safety = check_message_with_ai(data['message'])
    if not safety['safe']:
        db.session.add(BlockedLog(user_nickname=data['username'], content=data['message'], reason=safety['reason']))
        db.session.commit()
        if safety['reason'] == "harm":
            emit('receive_message', {'msg': data['message'], 'user': data['username']}, room=data['room'])
            emit('warning_popup', {'text': safety['alert']}, to=request.sid)
        else:
            emit('system_message', {'msg': f'🚫 {safety["alert"]}'}, to=request.sid)
    else:
        db.session.add(Message(sender=data['username'], room=data['room'], content=data['message']))
        db.session.commit()
        emit('receive_message', {'msg': data['message'], 'user': data['username']}, room=data['room'])

if __name__ == '__main__':
    socketio.run(app, debug=True, allow_unsafe_werkzeug=True)
