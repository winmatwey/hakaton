from flask import Flask, render_template, request, jsonify, redirect, url_for, session
import sqlite3, os, threading, time, uuid
from datetime import datetime, timedelta
from contextlib import contextmanager

BASE_DIR   = os.path.dirname(os.path.abspath(__file__))
DB_PATH    = os.path.join(BASE_DIR, 'instance', 'quest.db')
UPLOAD_DIR = os.path.join(BASE_DIR, 'static', 'uploads')

os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
os.makedirs(UPLOAD_DIR, exist_ok=True)

app = Flask(__name__, static_folder=os.path.join(BASE_DIR, 'static'))
app.secret_key = 'eng_quest_2024_secret'
app.config['SESSION_COOKIE_SAMESITE']    = 'Lax'
app.config['SESSION_COOKIE_HTTPONLY']    = True
app.config['SESSION_COOKIE_SECURE']      = False
app.config['PERMANENT_SESSION_LIFETIME'] = 86400

_nfc_lock = threading.Lock()
_nfc_last  = {'uid': None, 'ts': 0.0}

@contextmanager
def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

def row(r):   return dict(r) if r else None
def rows(rs): return [dict(r) for r in rs]
def now_str(): return datetime.now().strftime('%Y-%m-%d %H:%M:%S')

def init_db():
    with db() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS Teams (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            Login                TEXT    UNIQUE NOT NULL,
            Password             TEXT    NOT NULL,
            Role                 TEXT    NOT NULL DEFAULT 'Участник',
            Class                TEXT    NOT NULL DEFAULT '7-9',
            Start_Budget         INTEGER NOT NULL DEFAULT 500,
            Spent                INTEGER NOT NULL DEFAULT 0,
            Bridge_Weight        REAL    NOT NULL DEFAULT 0.0,
            Quantity_Assignments INTEGER NOT NULL DEFAULT 0,
            Status               TEXT    NOT NULL DEFAULT 'Не приступили к заданию'
        );
        CREATE TABLE IF NOT EXISTS Shop (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            Item_Name   TEXT    NOT NULL,
            Price       INTEGER NOT NULL,
            Image       TEXT    NOT NULL DEFAULT '',
            Stock       INTEGER NOT NULL DEFAULT 0,
            Item_Weight REAL    NOT NULL DEFAULT 0.0,
            Barcode     TEXT    NOT NULL DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS Orders_Shop (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            Team_Name      TEXT    NOT NULL,
            Item           TEXT    NOT NULL,
            Quantity       INTEGER NOT NULL,
            Cost           INTEGER NOT NULL,
            Time           TEXT    NOT NULL,
            Status         TEXT    NOT NULL DEFAULT 'Новый',
            Payment_Method TEXT    NOT NULL DEFAULT 'Ручной'
        );
        CREATE TABLE IF NOT EXISTS Assignments (
            id       INTEGER PRIMARY KEY AUTOINCREMENT,
            Class    TEXT    NOT NULL,
            Subject  TEXT    NOT NULL,
            Question TEXT    NOT NULL DEFAULT '',
            Image    TEXT    NOT NULL DEFAULT '',
            Answer   TEXT    NOT NULL,
            Price    INTEGER NOT NULL DEFAULT 30
        );
        CREATE TABLE IF NOT EXISTS NFC_Cards (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            UID           TEXT UNIQUE NOT NULL,
            Team_Login    TEXT NOT NULL,
            Registered_At TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS Reservations (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            Team_Name  TEXT    NOT NULL,
            Item_Id    INTEGER NOT NULL,
            Item_Name  TEXT    NOT NULL,
            Quantity   INTEGER NOT NULL,
            Total_Cost INTEGER NOT NULL,
            Created_At TEXT    NOT NULL,
            Expires_At TEXT    NOT NULL,
            Status     TEXT    NOT NULL DEFAULT 'Активна'
        );
        CREATE TABLE IF NOT EXISTS Settings (
            key   TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS Game (
            id         INTEGER PRIMARY KEY CHECK (id=1),
            status     TEXT NOT NULL DEFAULT 'idle',
            started_at TEXT,
            ends_at    TEXT,
            duration_s INTEGER NOT NULL DEFAULT 3600
        );
        INSERT OR IGNORE INTO Game(id,status) VALUES(1,'idle');
        """)
        c.execute("INSERT OR IGNORE INTO Settings(key,value) VALUES('reservation_ttl_minutes','5')")
        c.execute("INSERT OR IGNORE INTO Settings(key,value) VALUES('refresh_interval_s','10')")

def seed_db():
    with db() as c:
        if c.execute("SELECT 1 FROM Teams LIMIT 1").fetchone():
            return
        c.executemany("INSERT INTO Teams(Login,Password,Role,Class,Start_Budget) VALUES(?,?,?,?,?)", [
            ('admin',       'admin123', 'Администратор', '10-11', 500),
            ('shop',        'shop123',  'Магазин',       '10-11', 0),
            ('Мостовики',   'pass1', 'Участник', '10-11', 500),
            ('Архитекторы', 'pass2', 'Участник', '10-11', 500),
            ('Строители',   'pass3', 'Участник', '7-9',   500),
            ('Инженеры',    'pass4', 'Участник', '7-9',   500),
        ])
        c.executemany("INSERT INTO Shop(Item_Name,Price,Image,Stock,Item_Weight,Barcode) VALUES(?,?,?,?,?,?)", [
            ('Деревянные палочки', 50,  '', 10, 2.5,  'ITEM001'),
            ('Клей ПВА',          100, '', 10, 50.0, 'ITEM002'),
            ('Макароны',           15, '', 30, 5.0,  'ITEM003'),
            ('Скотч',              30, '', 20, 10.0, 'ITEM004'),
            ('Нитки',              20, '', 25, 1.0,  'ITEM005'),
        ])
        c.executemany("INSERT INTO Assignments(Class,Subject,Question,Image,Answer,Price) VALUES(?,?,?,?,?,?)", [
            ('7-9','Физика','Чему равно ускорение свободного падения на Земле (м/с²)?','','10',20),
            ('7-9','Физика','Как называется единица измерения силы в СИ?','','ньютон',20),
            ('7-9','Физика','Запишите формулу давления через силу F и площадь S.','','p=f/s',20),
            ('7-9','Физика','Скорость света в вакууме (округлённо, млн км/с)?','','300',25),
            ('7-9','Физика','Как называется изменение направления света при переходе между средами?','','преломление',25),
            ('7-9','Математика','Чему равна сумма углов треугольника (в градусах)?','','180',20),
            ('7-9','Математика','Как называется отношение длины окружности к диаметру?','','пи',20),
            ('7-9','Математика','Чему равен корень квадратный из 144?','','12',20),
            ('7-9','Математика','Теорема Пифагора: a²+b²=?','','c²',25),
            ('7-9','Математика','Сколько осей симметрии у правильного шестиугольника?','','6',25),
            ('7-9','Информатика','Сколько бит в одном байте?','','8',20),
            ('7-9','Информатика','Переведите 1010 из двоичной в десятичную.','','10',20),
            ('7-9','Информатика','Как расшифровывается CPU?','','центральный процессор',25),
            ('7-9','Информатика','Какой логический оператор истинен только при двух истинных операндах?','','и',20),
            ('7-9','Информатика','Сколько Кбайт в 1 Мбайт?','','1024',25),
            ('10-11','Физика','Запишите формулу второго закона Ньютона (F=?).','','ma',30),
            ('10-11','Физика','Как называется закон: U=IR?','','закон ома',30),
            ('10-11','Физика','Чему равна первая космическая скорость (км/с)?','','8',35),
            ('10-11','Физика','Формула кинетической энергии (Ek=?).','','mv²/2',30),
            ('10-11','Физика','Что характеризует постоянная Планка? (область физики)','','квантовая механика',35),
            ('10-11','Математика','Производная функции f(x)=x³?','','3x²',30),
            ('10-11','Математика','Чему равен sin(90°)?','','1',25),
            ('10-11','Математика','Формула дискриминанта квадратного уравнения (D=?).','','b²-4ac',35),
            ('10-11','Математика','Сколько способов выбрать 2 из 5? (сочетания C(5,2))','','10',35),
            ('10-11','Математика','Чему равен log₂(8)?','','3',30),
            ('10-11','Информатика','Какая структура данных работает по принципу LIFO?','','стек',30),
            ('10-11','Информатика','Переведите 255 из десятичной в шестнадцатеричную.','','ff',35),
            ('10-11','Информатика','Сложность пузырьковой сортировки в худшем случае (запишите как n²).','','n²',30),
            ('10-11','Информатика','Как расшифровывается HTTP?','','hypertext transfer protocol',30),
            ('10-11','Информатика','Что такое рекурсия? (одно слово — тип вызова функции)','','самовызов',35),
        ])

# ── helpers ──

def current_user():
    session.permanent = True
    tid = session.get('team_id')
    if not tid: return None
    with db() as c:
        return row(c.execute("SELECT * FROM Teams WHERE id=?", (tid,)).fetchone())

def reserved_qty(c, item_id):
    r = c.execute(
        "SELECT COALESCE(SUM(Quantity),0) FROM Reservations WHERE Item_Id=? AND Status='Активна'",
        (item_id,)).fetchone()
    return r[0] if r else 0

def available_qty(c, item_id):
    it = row(c.execute("SELECT Stock FROM Shop WHERE id=?", (item_id,)).fetchone())
    return max(0, it['Stock'] - reserved_qty(c, item_id)) if it else 0

def expire_reservations(c):
    c.execute("UPDATE Reservations SET Status='Просрочена' WHERE Status='Активна' AND Expires_At <= ?",
              (now_str(),))

def get_ttl(c):
    r = c.execute("SELECT value FROM Settings WHERE key='reservation_ttl_minutes'").fetchone()
    try:    return int(r[0]) * 60
    except: return 300

def get_game(c):
    return row(c.execute("SELECT * FROM Game WHERE id=1").fetchone())

# ── auth ──

@app.route('/')
def index(): return redirect(url_for('login'))

@app.route('/login', methods=['GET','POST'])
def login():
    if request.method == 'POST':
        d         = request.get_json() if request.is_json else request.form
        login_val = (d.get('login') or '').strip()
        pass_val  = (d.get('password') or '').strip()
        with db() as c:
            u = row(c.execute("SELECT * FROM Teams WHERE Login=? AND Password=?",
                              (login_val, pass_val)).fetchone())
        if u:
            if u['Role'] == 'Участник':
                with db() as gc:
                    g = get_game(gc)
                if g['status'] == 'idle':
                    err = 'Игра ещё не началась. Ожидайте сигнала организатора.'
                    return (jsonify({'ok': False, 'error': err}) if request.is_json
                            else render_template('login.html', error=err))
            session.permanent  = True
            session['team_id']   = u['id']
            session['team_role'] = u['Role']
            if request.is_json:
                return jsonify({'ok': True, 'role': u['Role']})
            if u['Role'] == 'Магазин':       return redirect(url_for('shop_page'))
            if u['Role'] == 'Администратор': return redirect(url_for('admin_page'))
            return redirect(url_for('team_page'))
        err = 'Неверный логин или пароль'
        return (jsonify({'ok': False, 'error': err}) if request.is_json
                else render_template('login.html', error=err))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('login') + '?logout=1')

# ── team ──

@app.route('/team')
def team_page():
    u = current_user()
    if not u or u['Role'] != 'Участник': return redirect(url_for('login'))
    u['Current_Balance'] = u['Start_Budget'] - u['Spent']
    with db() as c:
        expire_reservations(c)
        assignments  = rows(c.execute("SELECT * FROM Assignments WHERE Class=? ORDER BY Subject,id",
                                      (u['Class'],)).fetchall())
        orders       = rows(c.execute("SELECT * FROM Orders_Shop WHERE Team_Name=? ORDER BY Time DESC LIMIT 20",
                                      (u['Login'],)).fetchall())
        nfc_card     = row(c.execute("SELECT * FROM NFC_Cards WHERE Team_Login=?",
                                     (u['Login'],)).fetchone())
        shop_items   = rows(c.execute("SELECT * FROM Shop WHERE Stock>0 ORDER BY Item_Name").fetchall())
        for it in shop_items: it['available'] = available_qty(c, it['id'])
        reservations = rows(c.execute("SELECT * FROM Reservations WHERE Team_Name=? ORDER BY Created_At DESC",
                                      (u['Login'],)).fetchall())
    return render_template('team.html', team=u, assignments=assignments, orders=orders,
                           nfc_card=nfc_card, items=shop_items, reservations=reservations)

@app.route('/api/team/info')
def team_info():
    u = current_user()
    if not u: return jsonify({'ok': False})
    with db() as c:
        card = row(c.execute("SELECT UID FROM NFC_Cards WHERE Team_Login=?", (u['Login'],)).fetchone())
    return jsonify({'ok': True, 'balance': u['Start_Budget']-u['Spent'], 'spent': u['Spent'],
                    'weight': u['Bridge_Weight'], 'assignments': u['Quantity_Assignments'],
                    'start_budget': u['Start_Budget'], 'nfc_uid': card['UID'] if card else None})

@app.route('/api/team/submit_answer', methods=['POST'])
def submit_answer():
    u = current_user()
    if not u or u['Role'] != 'Участник': return jsonify({'ok': False, 'error': 'Нет доступа'})
    d      = request.get_json() or {}
    answer = (d.get('answer') or '').strip().lower()
    aid    = d.get('assignment_id')
    with db() as c:
        a = row(c.execute("SELECT * FROM Assignments WHERE id=?", (aid,)).fetchone())
        if not a: return jsonify({'ok': False, 'error': 'Задание не найдено'})
        if a['Answer'].strip().lower() == answer:
            new_budget = u['Start_Budget'] + a['Price']
            c.execute("UPDATE Teams SET Start_Budget=?,Quantity_Assignments=Quantity_Assignments+1,"
                      "Status='Начали выполнение задания' WHERE id=?", (new_budget, u['id']))
            return jsonify({'ok': True, 'points': a['Price'], 'balance': new_budget - u['Spent']})
    return jsonify({'ok': False, 'error': 'Неверный ответ'})

@app.route('/api/team/reserve', methods=['POST'])
def team_reserve():
    u = current_user()
    if not u or u['Role'] != 'Участник': return jsonify({'ok': False, 'error': 'Нет доступа'})
    d    = request.get_json() or {}
    cart = d.get('cart', [])
    with db() as c:
        expire_reservations(c)
        if c.execute("SELECT 1 FROM Reservations WHERE Team_Name=? AND Status='Активна'",
                     (u['Login'],)).fetchone():
            return jsonify({'ok': False, 'error': 'У вас уже есть активная бронь.'})
        balance = u['Start_Budget'] - u['Spent']
        total   = 0
        lines   = []
        for entry in cart:
            it  = row(c.execute("SELECT * FROM Shop WHERE id=?", (entry.get('item_id'),)).fetchone())
            qty = int(entry.get('quantity', 0))
            if not it or qty <= 0: continue
            avail = available_qty(c, it['id'])
            if avail < qty:
                return jsonify({'ok': False,
                    'error': f'Недостаточно «{it["Item_Name"]}»: доступно {avail}, запрошено {qty}'})
            total += it['Price'] * qty
            lines.append((it, qty))
        if not lines:       return jsonify({'ok': False, 'error': 'Корзина пуста'})
        if balance < total: return jsonify({'ok': False,
            'error': f'Недостаточно очков. Нужно: {total}, есть: {balance}'})
        ttl     = get_ttl(c)
        now     = now_str()
        expires = (datetime.now() + timedelta(seconds=ttl)).strftime('%Y-%m-%d %H:%M:%S')
        ids = []
        for it, qty in lines:
            c.execute("INSERT INTO Reservations(Team_Name,Item_Id,Item_Name,Quantity,Total_Cost,"
                      "Created_At,Expires_At,Status) VALUES(?,?,?,?,?,?,?,?)",
                      (u['Login'], it['id'], it['Item_Name'], qty, it['Price']*qty, now, expires, 'Активна'))
            ids.append(c.execute("SELECT last_insert_rowid()").fetchone()[0])
        if u['Status'] == 'Не приступили к заданию':
            c.execute("UPDATE Teams SET Status='Начали выполнение задания' WHERE id=?", (u['id'],))
    return jsonify({'ok': True, 'reservation_ids': ids, 'total': total,
                    'expires_at': expires, 'ttl_seconds': ttl})

@app.route('/api/team/cancel_reservation/<int:rid>', methods=['POST'])
def cancel_reservation(rid):
    u = current_user()
    if not u or u['Role'] != 'Участник': return jsonify({'ok': False, 'error': 'Нет доступа'})
    with db() as c:
        r = row(c.execute("SELECT * FROM Reservations WHERE id=?", (rid,)).fetchone())
        if not r:                          return jsonify({'ok': False, 'error': 'Бронь не найдена'})
        if r['Team_Name'] != u['Login']:   return jsonify({'ok': False, 'error': 'Это не ваша бронь'})
        if r['Status'] != 'Активна':       return jsonify({'ok': False, 'error': 'Бронь уже не активна'})
        c.execute("UPDATE Reservations SET Status='Отменена' WHERE id=?", (rid,))
    return jsonify({'ok': True})

# ── nfc ──

@app.route('/api/nfc/push', methods=['POST'])
def nfc_push():
    d   = request.get_json(silent=True) or {}
    uid = (d.get('uid') or '').strip().upper()
    if not uid: return jsonify({'ok': False, 'error': 'uid required'})
    with _nfc_lock:
        _nfc_last['uid'] = uid
        _nfc_last['ts']  = time.time()
    return jsonify({'ok': True, 'uid': uid})

@app.route('/api/nfc/poll')
def nfc_poll():
    since = float(request.args.get('since', 0))
    with _nfc_lock:
        uid = _nfc_last['uid']
        ts  = _nfc_last['ts']
    if not uid or ts <= since: return jsonify({'ok': False})
    with db() as c:
        expire_reservations(c)
        card = row(c.execute("SELECT * FROM NFC_Cards WHERE UID=?", (uid,)).fetchone())
        if not card:
            return jsonify({'ok': True, 'uid': uid, 'ts': ts, 'team': None, 'unbound': True})
        team = row(c.execute("SELECT * FROM Teams WHERE Login=?", (card['Team_Login'],)).fetchone())
        if not team:
            return jsonify({'ok': True, 'uid': uid, 'ts': ts, 'team': None, 'unbound': True})
        res = rows(c.execute("SELECT * FROM Reservations WHERE Team_Name=? AND Status='Активна' ORDER BY Created_At",
                             (team['Login'],)).fetchall())
    return jsonify({'ok': True, 'uid': uid, 'ts': ts,
        'team': {'name': team['Login'], 'balance': team['Start_Budget']-team['Spent'], 'class': team['Class']},
        'reservations': res})

@app.route('/api/nfc/register', methods=['POST'])
def nfc_register():
    u = current_user()
    if not u or u['Role'] not in ('Администратор','Магазин'):
        return jsonify({'ok': False, 'error': 'Нет доступа'})
    d          = request.get_json() or {}
    uid        = (d.get('uid') or '').strip().upper()
    team_login = (d.get('team_login') or '').strip()
    if not uid or not team_login: return jsonify({'ok': False, 'error': 'uid и team_login обязательны'})
    with db() as c:
        if not c.execute("SELECT 1 FROM Teams WHERE Login=?", (team_login,)).fetchone():
            return jsonify({'ok': False, 'error': 'Команда не найдена'})
        c.execute("DELETE FROM NFC_Cards WHERE Team_Login=?", (team_login,))
        c.execute("DELETE FROM NFC_Cards WHERE UID=?", (uid,))
        c.execute("INSERT INTO NFC_Cards(UID,Team_Login,Registered_At) VALUES(?,?,?)",
                  (uid, team_login, now_str()))
    return jsonify({'ok': True, 'uid': uid, 'team': team_login})

@app.route('/api/nfc/list')
def nfc_list():
    u = current_user()
    if not u or u['Role'] not in ('Администратор','Магазин'): return jsonify({'ok': False})
    with db() as c:
        cards = rows(c.execute("SELECT * FROM NFC_Cards ORDER BY Team_Login").fetchall())
    return jsonify({'ok': True, 'cards': cards})

@app.route('/api/nfc/delete/<int:cid>', methods=['DELETE'])
def nfc_delete(cid):
    u = current_user()
    if not u or u['Role'] != 'Администратор': return jsonify({'ok': False})
    with db() as c: c.execute("DELETE FROM NFC_Cards WHERE id=?", (cid,))
    return jsonify({'ok': True})

# ── shop ──

@app.route('/shop')
def shop_page():
    u = current_user()
    if not u or u['Role'] != 'Магазин': return redirect(url_for('login'))
    with db() as c:
        expire_reservations(c)
        items        = rows(c.execute("SELECT * FROM Shop ORDER BY Item_Name").fetchall())
        orders       = rows(c.execute("SELECT * FROM Orders_Shop WHERE Status='Новый' ORDER BY Time DESC").fetchall())
        reservations = rows(c.execute("SELECT * FROM Reservations WHERE Status='Активна' ORDER BY Created_At").fetchall())
        for it in items: it['available'] = available_qty(c, it['id'])
    return render_template('shop.html', items=items, orders=orders, reservations=reservations)

@app.route('/api/shop/orders')
def shop_orders():
    u = current_user()
    if not u or u['Role'] != 'Магазин': return jsonify({'ok': False})
    with db() as c:
        os_ = rows(c.execute("SELECT * FROM Orders_Shop WHERE Status='Новый' ORDER BY Time DESC").fetchall())
    return jsonify({'ok': True, 'orders': [
        {'id': o['id'], 'team': o['Team_Name'], 'item': o['Item'], 'qty': o['Quantity'],
         'cost': o['Cost'], 'payment': o['Payment_Method'],
         'time': o['Time'][11:16] if len(o['Time'])>10 else o['Time']} for o in os_]})

@app.route('/api/shop/reservations')
def shop_reservations():
    u = current_user()
    if not u or u['Role'] not in ('Магазин','Администратор'): return jsonify({'ok': False})
    with db() as c:
        expire_reservations(c)
        res = rows(c.execute("SELECT * FROM Reservations WHERE Status='Активна' ORDER BY Created_At").fetchall())
    return jsonify({'ok': True, 'reservations': res})

@app.route('/api/shop/barcode/<path:code>')
def barcode_lookup(code):
    u = current_user()
    if not u or u['Role'] != 'Магазин': return jsonify({'ok': False, 'error': 'Нет доступа'})
    with db() as c:
        it = row(c.execute("SELECT * FROM Shop WHERE Barcode=? OR CAST(id AS TEXT)=?", (code, code)).fetchone())
        if not it: return jsonify({'ok': False, 'error': f'Товар «{code}» не найден'})
        it['available'] = available_qty(c, it['id'])
    return jsonify({'ok': True, 'item': {'id': it['id'], 'name': it['Item_Name'], 'price': it['Price'],
        'stock': it['Stock'], 'available': it['available'], 'weight': it['Item_Weight'], 'barcode': it['Barcode']}})

@app.route('/api/shop/scan', methods=['POST'])
def shop_scan():
    u = current_user()
    if not u or u['Role'] != 'Магазин': return jsonify({'ok': False, 'error': 'Нет доступа'})
    d  = request.get_json() or {}
    tl = (d.get('team_login') or '').strip()
    with db() as c:
        expire_reservations(c)
        team = row(c.execute("SELECT * FROM Teams WHERE Login=?", (tl,)).fetchone())
        if not team: return jsonify({'ok': False, 'error': f'Команда «{tl}» не найдена'})
        items = rows(c.execute("SELECT * FROM Shop WHERE Stock>0 ORDER BY Item_Name").fetchall())
        for it in items: it['available'] = available_qty(c, it['id'])
        res = rows(c.execute("SELECT * FROM Reservations WHERE Team_Name=? AND Status='Активна'", (tl,)).fetchall())
    return jsonify({'ok': True,
        'team': {'name': team['Login'], 'balance': team['Start_Budget']-team['Spent'], 'class': team['Class']},
        'items': [{'id': i['id'], 'name': i['Item_Name'], 'price': i['Price'], 'stock': i['Stock'],
                   'available': i['available'], 'weight': i['Item_Weight'], 'barcode': i['Barcode']} for i in items],
        'reservations': res})

def _do_purchase(c, team_login, cart, payment='Ручной'):
    team = row(c.execute("SELECT * FROM Teams WHERE Login=?", (team_login,)).fetchone())
    if not team: return False, 'Команда не найдена'
    balance = team['Start_Budget'] - team['Spent']
    total = 0; weight = 0.0; lines = []
    for entry in cart:
        it  = row(c.execute("SELECT * FROM Shop WHERE id=?", (entry['item_id'],)).fetchone())
        qty = int(entry['quantity'])
        if not it or qty <= 0: continue
        if it['Stock'] < qty: return False, f'Недостаточно «{it["Item_Name"]}» на складе'
        cost = it['Price'] * qty
        total += cost; weight += it['Item_Weight'] * qty
        lines.append((it, qty, cost))
    if not lines:       return False, 'Корзина пуста'
    if balance < total: return False, f'Недостаточно очков. Нужно: {total}, есть: {balance}'
    now = now_str()
    for it, qty, cost in lines:
        c.execute("UPDATE Shop SET Stock=Stock-? WHERE id=?", (qty, it['id']))
        c.execute("INSERT INTO Orders_Shop(Team_Name,Item,Quantity,Cost,Time,Status,Payment_Method) VALUES(?,?,?,?,?,?,?)",
                  (team_login, it['Item_Name'], qty, cost, now, 'Новый', payment))
    c.execute("UPDATE Teams SET Spent=Spent+?,Bridge_Weight=Bridge_Weight+?,"
              "Status='Начали выполнение задания' WHERE Login=?", (total, weight, team_login))
    return True, {'total': total, 'new_balance': balance-total, 'team': team_login}

@app.route('/api/shop/buy', methods=['POST'])
def shop_buy():
    u = current_user()
    if not u or u['Role'] != 'Магазин': return jsonify({'ok': False, 'error': 'Нет доступа'})
    d = request.get_json() or {}
    with db() as c:
        ok, res = _do_purchase(c, (d.get('team_login') or '').strip(),
                               d.get('cart', []), d.get('payment_method','Ручной'))
    return jsonify({'ok': ok, **(res if ok else {'error': res})})

@app.route('/api/shop/nfc_buy', methods=['POST'])
def nfc_buy():
    u = current_user()
    if not u or u['Role'] != 'Магазин': return jsonify({'ok': False, 'error': 'Нет доступа'})
    d   = request.get_json() or {}
    uid = (d.get('uid') or '').strip().upper()
    if not uid: return jsonify({'ok': False, 'error': 'uid обязателен'})
    with db() as c:
        card = row(c.execute("SELECT * FROM NFC_Cards WHERE UID=?", (uid,)).fetchone())
        if not card: return jsonify({'ok': False, 'error': f'Карта {uid} не привязана'})
        ok, res = _do_purchase(c, card['Team_Login'], d.get('cart', []), 'NFC')
    return jsonify({'ok': ok, **(res if ok else {'error': res})})

@app.route('/api/shop/pay_reservation', methods=['POST'])
def pay_reservation():
    u = current_user()
    if not u or u['Role'] != 'Магазин': return jsonify({'ok': False, 'error': 'Нет доступа'})
    d       = request.get_json() or {}
    res_ids = d.get('reservation_ids', [])
    payment = d.get('payment_method','Ручной')
    uid     = (d.get('uid') or '').strip().upper()
    if not res_ids: return jsonify({'ok': False, 'error': 'Не указаны брони'})
    with db() as c:
        expire_reservations(c)
        team_name = None; reservations = []
        for rid in res_ids:
            r = row(c.execute("SELECT * FROM Reservations WHERE id=?", (rid,)).fetchone())
            if not r: return jsonify({'ok': False, 'error': f'Бронь #{rid} не найдена'})
            if r['Status'] != 'Активна': return jsonify({'ok': False, 'error': f'Бронь #{rid} просрочена'})
            if team_name and r['Team_Name'] != team_name:
                return jsonify({'ok': False, 'error': 'Брони разных команд'})
            team_name = r['Team_Name']; reservations.append(r)
        if payment == 'NFC' and uid:
            card = row(c.execute("SELECT * FROM NFC_Cards WHERE UID=?", (uid,)).fetchone())
            if not card: return jsonify({'ok': False, 'error': f'Карта {uid} не привязана'})
            if card['Team_Login'] != team_name:
                return jsonify({'ok': False, 'error': f'Карта «{card["Team_Login"]}», бронь «{team_name}»'})
        team = row(c.execute("SELECT * FROM Teams WHERE Login=?", (team_name,)).fetchone())
        if not team: return jsonify({'ok': False, 'error': 'Команда не найдена'})
        balance = team['Start_Budget'] - team['Spent']
        total   = sum(r['Total_Cost'] for r in reservations)
        weight  = 0.0
        if balance < total: return jsonify({'ok': False, 'error': f'Недостаточно очков: {total}, есть {balance}'})
        now = now_str()
        for r in reservations:
            it = row(c.execute("SELECT * FROM Shop WHERE id=?", (r['Item_Id'],)).fetchone())
            if it:
                if it['Stock'] < r['Quantity']:
                    return jsonify({'ok': False, 'error': f'Нет «{it["Item_Name"]}» на складе'})
                c.execute("UPDATE Shop SET Stock=Stock-? WHERE id=?", (r['Quantity'], it['id']))
                weight += it['Item_Weight'] * r['Quantity']
            c.execute("INSERT INTO Orders_Shop(Team_Name,Item,Quantity,Cost,Time,Status,Payment_Method) VALUES(?,?,?,?,?,?,?)",
                      (team_name, r['Item_Name'], r['Quantity'], r['Total_Cost'], now, 'Новый', payment))
            c.execute("UPDATE Reservations SET Status='Оплачена' WHERE id=?", (r['id'],))
        c.execute("UPDATE Teams SET Spent=Spent+?,Bridge_Weight=Bridge_Weight+?,"
                  "Status='Начали выполнение задания' WHERE Login=?", (total, weight, team_name))
    return jsonify({'ok': True, 'team': team_name, 'total': total, 'new_balance': balance-total})

@app.route('/api/shop/cancel_reservation/<int:rid>', methods=['POST'])
def shop_cancel_reservation(rid):
    u = current_user()
    if not u or u['Role'] != 'Магазин': return jsonify({'ok': False})
    with db() as c:
        c.execute("UPDATE Reservations SET Status='Отменена' WHERE id=? AND Status='Активна'", (rid,))
    return jsonify({'ok': True})

@app.route('/api/shop/issue_order', methods=['POST'])
def issue_order():
    u = current_user()
    if not u or u['Role'] != 'Магазин': return jsonify({'ok': False})
    d = request.get_json() or {}
    with db() as c: c.execute("UPDATE Orders_Shop SET Status='Выдан' WHERE id=?", (d.get('order_id'),))
    return jsonify({'ok': True})

# ── admin ──

@app.route('/admin')
def admin_page():
    u = current_user()
    if not u or u['Role'] != 'Администратор': return redirect(url_for('login'))
    with db() as c:
        expire_reservations(c)
        teams        = rows(c.execute("SELECT * FROM Teams WHERE Role='Участник' ORDER BY Login").fetchall())
        items        = rows(c.execute("SELECT * FROM Shop ORDER BY Item_Name").fetchall())
        assignments  = rows(c.execute("SELECT * FROM Assignments ORDER BY Class,Subject,id").fetchall())
        orders       = rows(c.execute("SELECT * FROM Orders_Shop ORDER BY Time DESC").fetchall())
        nfc_cards    = rows(c.execute("SELECT * FROM NFC_Cards ORDER BY Team_Login").fetchall())
        reservations = rows(c.execute("SELECT * FROM Reservations WHERE Status='Активна' ORDER BY Created_At").fetchall())
        for t in teams: t['Current_Balance'] = t['Start_Budget'] - t['Spent']
        for it in items: it['available'] = available_qty(c, it['id'])
    return render_template('admin.html', teams=teams, items=items, assignments=assignments,
                           orders=orders, nfc_cards=nfc_cards, reservations=reservations)

@app.route('/api/admin/add_team', methods=['POST'])
def admin_add_team():
    u = current_user()
    if not u or u['Role'] != 'Администратор': return jsonify({'ok': False})
    d = request.get_json() or {}
    with db() as c:
        c.execute("INSERT INTO Teams(Login,Password,Role,Class,Start_Budget) VALUES(?,?,?,?,?)",
                  (d['login'], d['password'], d.get('role','Участник'), d.get('class','7-9'), int(d.get('budget',500))))
        tid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    return jsonify({'ok': True, 'id': tid})

@app.route('/api/admin/delete_team/<int:tid>', methods=['DELETE'])
def admin_delete_team(tid):
    u = current_user()
    if not u or u['Role'] != 'Администратор': return jsonify({'ok': False})
    with db() as c: c.execute("DELETE FROM Teams WHERE id=?", (tid,))
    return jsonify({'ok': True})

@app.route('/api/admin/add_item', methods=['POST'])
def admin_add_item():
    u = current_user()
    if not u or u['Role'] != 'Администратор': return jsonify({'ok': False})
    d = request.get_json() or {}
    with db() as c:
        c.execute("INSERT INTO Shop(Item_Name,Price,Image,Stock,Item_Weight,Barcode) VALUES(?,?,?,?,?,?)",
                  (d['name'], int(d['price']), d.get('image',''), int(d.get('stock',0)),
                   float(d.get('weight',0)), d.get('barcode','')))
        iid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    return jsonify({'ok': True, 'id': iid})

@app.route('/api/admin/update_item/<int:iid>', methods=['PUT'])
def admin_update_item(iid):
    u = current_user()
    if not u or u['Role'] != 'Администратор': return jsonify({'ok': False})
    d = request.get_json() or {}
    with db() as c:
        c.execute("UPDATE Shop SET Item_Name=?,Price=?,Image=?,Stock=?,Item_Weight=?,Barcode=? WHERE id=?",
                  (d['name'], int(d['price']), d.get('image',''), int(d['stock']),
                   float(d['weight']), d.get('barcode',''), iid))
    return jsonify({'ok': True})

@app.route('/api/admin/delete_item/<int:iid>', methods=['DELETE'])
def admin_delete_item(iid):
    u = current_user()
    if not u or u['Role'] != 'Администратор': return jsonify({'ok': False})
    with db() as c: c.execute("DELETE FROM Shop WHERE id=?", (iid,))
    return jsonify({'ok': True})

@app.route('/api/admin/upload_image', methods=['POST'])
def admin_upload_image():
    u = current_user()
    if not u or u['Role'] != 'Администратор': return jsonify({'ok': False, 'error': 'Нет доступа'})
    if 'file' not in request.files: return jsonify({'ok': False, 'error': 'Файл не выбран'})
    f = request.files['file']
    if not f.filename: return jsonify({'ok': False, 'error': 'Файл не выбран'})
    ext = f.filename.rsplit('.', 1)[-1].lower() if '.' in f.filename else ''
    if ext not in {'png','jpg','jpeg','gif','webp'}:
        return jsonify({'ok': False, 'error': f'Формат .{ext} не поддерживается'})
    fname = uuid.uuid4().hex + '.' + ext
    f.save(os.path.join(UPLOAD_DIR, fname))
    return jsonify({'ok': True, 'url': '/static/uploads/' + fname})

@app.route('/api/admin/add_assignment', methods=['POST'])
def admin_add_assignment():
    u = current_user()
    if not u or u['Role'] != 'Администратор': return jsonify({'ok': False})
    d = request.get_json() or {}
    with db() as c:
        c.execute("INSERT INTO Assignments(Class,Subject,Question,Image,Answer,Price) VALUES(?,?,?,?,?,?)",
                  (d['class'], d['subject'], d.get('question',''), d.get('image',''), d['answer'], int(d.get('price',30))))
        aid = c.execute("SELECT last_insert_rowid()").fetchone()[0]
    return jsonify({'ok': True, 'id': aid})

@app.route('/api/admin/update_assignment/<int:aid>', methods=['PUT'])
def admin_update_assignment(aid):
    u = current_user()
    if not u or u['Role'] != 'Администратор': return jsonify({'ok': False})
    d = request.get_json() or {}
    with db() as c:
        c.execute("UPDATE Assignments SET Class=?,Subject=?,Question=?,Image=?,Answer=?,Price=? WHERE id=?",
                  (d['class'], d['subject'], d.get('question',''), d.get('image',''), d['answer'], int(d['price']), aid))
    return jsonify({'ok': True})

@app.route('/api/admin/delete_assignment/<int:aid>', methods=['DELETE'])
def admin_delete_assignment(aid):
    u = current_user()
    if not u or u['Role'] != 'Администратор': return jsonify({'ok': False})
    with db() as c: c.execute("DELETE FROM Assignments WHERE id=?", (aid,))
    return jsonify({'ok': True})

@app.route('/api/admin/leaderboard')
def admin_leaderboard():
    u = current_user()
    if not u or u['Role'] != 'Администратор': return jsonify({'ok': False})
    with db() as c:
        teams = rows(c.execute("SELECT * FROM Teams WHERE Role='Участник'").fetchall())
    teams.sort(key=lambda t: t['Start_Budget']-t['Spent'], reverse=True)
    return jsonify({'ok': True, 'teams': [
        {'id': t['id'], 'name': t['Login'], 'class': t['Class'],
         'balance': t['Start_Budget']-t['Spent'], 'spent': t['Spent'],
         'weight': t['Bridge_Weight'], 'assignments': t['Quantity_Assignments'],
         'status': t['Status']} for t in teams]})

@app.route('/api/settings/public')
def public_settings():
    with db() as c:
        r = c.execute("SELECT value FROM Settings WHERE key='refresh_interval_s'").fetchone()
    return jsonify({'ok': True, 'refresh_interval_s': int(r[0]) if r else 10})

@app.route('/api/admin/settings', methods=['GET','POST'])
def admin_settings():
    u = current_user()
    if not u or u['Role'] != 'Администратор': return jsonify({'ok': False})
    if request.method == 'GET':
        with db() as c:
            s = {r[0]: r[1] for r in c.execute("SELECT key,value FROM Settings").fetchall()}
        return jsonify({'ok': True, 'settings': s})
    d = request.get_json() or {}
    with db() as c:
        for k, v in d.items():
            c.execute("INSERT OR REPLACE INTO Settings(key,value) VALUES(?,?)", (k, str(v)))
    return jsonify({'ok': True})

@app.route('/api/admin/reset_all', methods=['POST'])
def admin_reset_all():
    u = current_user()
    if not u or u['Role'] != 'Администратор': return jsonify({'ok': False})
    with db() as c:
        c.execute("UPDATE Teams SET Spent=0,Bridge_Weight=0.0,Quantity_Assignments=0,"
                  "Start_Budget=500,Status='Не приступили к заданию' WHERE Role='Участник'")
        c.execute("DELETE FROM Orders_Shop")
        c.execute("UPDATE Reservations SET Status='Отменена' WHERE Status='Активна'")
    return jsonify({'ok': True})

# ── game ──

@app.route('/api/game/status')
def game_status():
    with db() as c:
        g = get_game(c)
    result = {'status': g['status']}
    if g['status'] == 'running' and g['ends_at']:
        ends = datetime.fromisoformat(g['ends_at'])
        left = max(0, (ends - datetime.now()).total_seconds())
        result.update({'ends_at': g['ends_at'], 'left_s': int(left), 'started_at': g['started_at']})
        if left <= 0:
            with db() as c2: c2.execute("UPDATE Game SET status='finished' WHERE id=1")
            result['status'] = 'finished'; result['left_s'] = 0
    return jsonify({'ok': True, **result})

@app.route('/api/admin/game/start', methods=['POST'])
def game_start():
    u = current_user()
    if not u or u['Role'] != 'Администратор': return jsonify({'ok': False})
    d          = request.get_json() or {}
    duration_s = max(60, min(172800, int(d.get('duration_s', 3600))))
    now        = datetime.now()
    ends       = now + timedelta(seconds=duration_s)
    with db() as c:
        c.execute("UPDATE Game SET status='running',started_at=?,ends_at=?,duration_s=? WHERE id=1",
                  (now.strftime('%Y-%m-%d %H:%M:%S'), ends.strftime('%Y-%m-%d %H:%M:%S'), duration_s))
    return jsonify({'ok': True, 'ends_at': ends.strftime('%Y-%m-%d %H:%M:%S'), 'duration_s': duration_s})

@app.route('/api/admin/game/stop', methods=['POST'])
def game_stop():
    u = current_user()
    if not u or u['Role'] != 'Администратор': return jsonify({'ok': False})
    with db() as c: c.execute("UPDATE Game SET status='finished',ends_at=? WHERE id=1", (now_str(),))
    return jsonify({'ok': True})

@app.route('/api/admin/game/reset', methods=['POST'])
def game_reset():
    u = current_user()
    if not u or u['Role'] != 'Администратор': return jsonify({'ok': False})
    with db() as c:
        c.execute("UPDATE Game SET status='idle',started_at=NULL,ends_at=NULL WHERE id=1")
        c.execute("UPDATE Teams SET Spent=0,Bridge_Weight=0.0,Quantity_Assignments=0,"
                  "Start_Budget=500,Status='Не приступили к заданию' WHERE Role='Участник'")
        c.execute("DELETE FROM Orders_Shop")
        c.execute("UPDATE Reservations SET Status='Отменена' WHERE Status='Активна'")
    return jsonify({'ok': True})

@app.route('/api/admin/score_build', methods=['POST'])
def score_build():
    u = current_user()
    if not u: return jsonify({'ok': False, 'error': 'SESSION_EXPIRED'})
    if u['Role'] != 'Администратор': return jsonify({'ok': False, 'error': 'Нет доступа'})
    d          = request.get_json() or {}
    team_login = (d.get('team_login') or '').strip()
    try: points = int(float(d.get('points', 0)))
    except: return jsonify({'ok': False, 'error': 'Неверное количество баллов'})
    if not team_login or points < 1:
        return jsonify({'ok': False, 'error': 'Неверные параметры'})
    with db() as c:
        team = row(c.execute("SELECT * FROM Teams WHERE Login=?", (team_login,)).fetchone())
        if not team: return jsonify({'ok': False, 'error': 'Команда не найдена'})
        c.execute("UPDATE Teams SET Start_Budget=Start_Budget+? WHERE Login=?", (points, team_login))
        new_bal = team['Start_Budget'] - team['Spent'] + points
    return jsonify({'ok': True, 'team': team_login, 'added': points, 'new_balance': new_bal})

if __name__ == '__main__':
    init_db()
    seed_db()
    app.run(debug=True, host='0.0.0.0', port=5000)
