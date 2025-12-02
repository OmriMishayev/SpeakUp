import os
import re
from flask import Flask, render_template_string, request, send_from_directory, jsonify
from flask_socketio import SocketIO, emit, join_room
from flask_sqlalchemy import SQLAlchemy
from flask_login import LoginManager, UserMixin, login_user, login_required, logout_user, current_user
import google.generativeai as genai

# --- הגדרות שרת ומסד נתונים ---
app = Flask(__name__)
app.config['SECRET_KEY'] = 'speakup_secret_key'
# הגדרת מיקום מסד הנתונים
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
socketio = SocketIO(app, cors_allowed_origins="*")
login_manager = LoginManager()
login_manager.init_app(app)

# --- הגדרת ה-AI של גוגל ---
GOOGLE_API_KEY = "AIzaSyAt5EIux3EauqPvQCHNatMGhdRynu5g2vY"
genai.configure(api_key=GOOGLE_API_KEY)
try:
    model = genai.GenerativeModel('gemini-2.0-flash')
except:
    model = genai.GenerativeModel('gemini-pro')

# --- מודל משתמש (טבלת SQL) ---
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    username = db.Column(db.String(100), unique=True, nullable=False)
    password = db.Column(db.String(100), nullable=False) # בפרויקט אמיתי מצפינים סיסמאות

# טעינת משתמש לזיכרון
@login_manager.user_loader
def load_user(user_id):
    return User.query.get(int(user_id))

# --- יצירת מסד הנתונים (רץ פעם אחת בהתחלה) ---
with app.app_context():
    db.create_all()

# --- בוט ההגנה (AI) ---
def check_message_with_ai(text):
    try:
        prompt = f"""
        You are a safety moderator. Analyze this message: [{text}]
        Classify it into ONE category:
        1. 'SAFE' - Normal conversation.
        2. 'SUICIDE' - Self-harm, depression, dying.
        3. 'PREDATOR' - Asking for personal info (phone, address), sexual harassment, meeting up.
        Reply ONLY with: SAFE, SUICIDE, or PREDATOR.
        """
        response = model.generate_content(prompt)
        result = response.text.strip().upper()
        print(f"AI Check: '{text}' -> '{result}'") 

        if "SUICIDE" in result:
            return {"safe": False, "reason": "harm", "alert": "זיהינו תוכן רגיש. כפתור תמיכה זמין עבורך."}
        if "PREDATOR" in result:
            return {"safe": False, "reason": "predator", "alert": "נחסם עקב חשד לתוכן פוגעני."}
        return {"safe": True, "reason": "ok", "alert": None}
    except:
        return {"safe": True, "reason": "error", "alert": None}

# --- נתיבים (Routes) ---

# טעינת לוגו ותמונות
@app.route('/<path:filename>')
def serve_static(filename):
    return send_from_directory('.', filename)

# דף הבית
@app.route('/')
def index():
    try:
        base_dir = os.path.dirname(os.path.abspath(__file__))
        html_path = os.path.join(base_dir, 'SPEAKUP1.html')
        with open(html_path, 'r', encoding='utf-8') as f:
            return render_template_string(f.read())
    except FileNotFoundError:
        return "Error: SPEAKUP1.html missing"

# --- הרשמה והתחברות (API) ---

@app.route('/register', methods=['POST'])
def register():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    if User.query.filter_by(username=username).first():
        return jsonify({'success': False, 'message': 'שם המשתמש תפוס!'})
    
    new_user = User(username=username, password=password)
    db.session.add(new_user)
    db.session.commit()
    
    login_user(new_user)
    return jsonify({'success': True, 'username': username})

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    username = data.get('username')
    password = data.get('password')
    
    user = User.query.filter_by(username=username).first()
    
    if user and user.password == password:
        login_user(user)
        return jsonify({'success': True, 'username': username})
    
    return jsonify({'success': False, 'message': 'שם משתמש או סיסמה שגויים'})

@app.route('/logout')
@login_required
def logout():
    logout_user()
    return jsonify({'success': True})

# --- ניהול הצ'אט ---
@socketio.on('join')
def handle_join(data):
    join_room(data['room'])
    emit('system_message', {'msg': f"{data['username']} הצטרף/ה לשיחה."}, room=data['room'])

@socketio.on('send_message')
def handle_message(data):
    safety = check_message_with_ai(data['message'])
    if not safety['safe']:
        if safety['reason'] == "harm":
            emit('receive_message', {'msg': data['message'], 'user': data['username']}, room=data['room'])
            emit('warning_popup', {'text': safety['alert']}, to=request.sid)
        else:
            emit('system_message', {'msg': f'🚫 {safety["alert"]}'}, to=request.sid)
    else:
        emit('receive_message', {'msg': data['message'], 'user': data['username']}, room=data['room'])

if __name__ == '__main__':
    print("AI Server + Database Running on http://127.0.0.1:5000")
    socketio.run(app, debug=True, allow_unsafe_werkzeug=True)