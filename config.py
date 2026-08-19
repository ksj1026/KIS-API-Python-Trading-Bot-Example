# ==========================================================
# [config.py] - 🌟 100% 통합 완성본 🌟 (Part 1)
# ⚠️ V_REV 도입에 따른 P매매 잔재 완벽 소각 버전
# 💡 [V24.10 수술] 동적 에스크로 락다운 깃발(Flag) 제어 로직 추가
# 💡 [V25.00 수술] AVWAP 하이브리드 전술 상태 저장(캐싱) 파일 경로 및 함수 이식
# 🚨 [V25.19 핫픽스] 빈 장부 스캔 시 IndexError 런타임 붕괴 완벽 방어
# 🚨 [V25.19 핫픽스] 에스크로(Escrow) 3대 관리 함수(set/add/clear) 팩트 기반 완전 구현
# 🚀 [V26.00 승격] 수동 VWAP 시그널 모드(Manual Mode) 독립 플래그 및 캐싱 엔진 신설 탑재
# 🚀 [V26.07 확정 순수익 렌더링 패치] 명예의 전당 및 졸업 카드 발급 시 한투 OpenAPI 왕복 수수료(0.5%) 완벽 차감 이식
# 🚨 [V27.10 그랜드 수술] 에스크로 캐시 영구 박제(Ghost Escrow 방어), 액면분할 수학적 반올림(Banker's Rounding) 오류 교정 및 fsync 무결성 확보
# 🚨 [V27.11 핫픽스] I/O FD 누수 방어, TOCTOU 경쟁 상태 원천 차단 래퍼 추가
# MODIFIED: [V28.25 그랜드 수술] 수수료 하드코딩 전면 소각 및 동적 수수료(Fee) 설정 엔진 탑재
# MODIFIED: [V28.26 타임존 락온 그랜드 수술] KST 기준 날짜 연산을 전면 폐기하고,
# INIT 레코드 기록 및 락(Lock) 해제 등 모든 기준 시간을 EST(미국 동부)로 100% 형변환하여 
# 타임 패러독스로 인한 스냅샷 매핑 실패 버그를 영구 소각 완료. (EC-3 방어)
# 🚨 [V28.50 NEW] 사용자 맞춤형 AVWAP 암살자 조기 퇴근 설정(Early Exit/Target) 저장소 완비
# ==========================================================
import json
import os
import datetime
import pytz
import math
import time
import shutil
import tempfile
import pandas_market_calendars as mcal

# NEW: 다중 스레드/프로세스 환경에서 락 및 에스크로 동기화 제어를 위한 모듈 임포트
import threading
try:
    import fcntl
except ImportError:
    fcntl = None

try:
    from version_history import VERSION_HISTORY
except ImportError:
    VERSION_HISTORY = ["V14.x [-] 버전 기록 파일(version_history.py)을 찾을 수 없습니다."]

class ConfigManager:
    def __init__(self):
        self.FILES = {
            "TOKEN": "data/token.dat",
            "CHAT_ID": "data/chat_id.dat",
            "LEDGER": "data/manual_ledger.json",    
            "HISTORY": "data/manual_history.json",  
            "SPLIT": "data/split_config.json",
            "TICKER": "data/active_tickers.json",
            "UPWARD_SNIPER": "data/upward_sniper.json", 
            "SECRET_MODE": "data/secret_mode.dat",
            "PROFIT_CFG": "data/profit_config.json",
            "LOCKS": "data/trade_locks.json",
            "SEED_CFG": "data/seed_config.json",         
            "COMPOUND_CFG": "data/compound_config.json",
            "VERSION_CFG": "data/version_config.json",
            "REVERSE_CFG": "data/reverse_config.json",
            "SNIPER_MULTIPLIER_CFG": "data/sniper_multiplier.json",
            "SPLIT_HISTORY": "data/split_history.json",
            "AVWAP_HYBRID_CFG": "data/avwap_hybrid.json",
            "MANUAL_VWAP_CFG": "data/manual_vwap_config.json",
            "FEE_CFG": "data/fee_config.json", # NEW: 동적 수수료 저장소 추가
            "AVWAP_EARLY_EXIT_CFG": "data/avwap_early_exit.json",    # 🚨 [V28.50] 조기 퇴근 듀얼 모드 스위치
            "AVWAP_EARLY_TARGET_CFG": "data/avwap_early_target.json", # 🚨 [V28.50] 조기 퇴근 목표 수익률 저장소
            "VR_CFG": "data/vr_config.json",  # NEW: VR5 밸류리밸런싱 설정 저장소
            "VR_PENDING": "data/vr_pending_orders.json",  # NEW: VR5 사다리 주문 승인 대기열
            "VR_LEDGER": "data/vr_ledger.json"  # NEW: VR5 KIS 체결내역 동기화 원장 (Pool 잔액 역산용)
        }
        
        self.DEFAULT_SEED = {"SOXL": 6720.0, "TQQQ": 6720.0}
        self.DEFAULT_SPLIT = {"SOXL": 40.0, "TQQQ": 40.0}
        self.DEFAULT_TARGET = {"SOXL": 12.0, "TQQQ": 10.0}
        self.DEFAULT_VERSION = {"SOXL": "V14", "TQQQ": "V14"}
        self.DEFAULT_COMPOUND = {"SOXL": 70.0, "TQQQ": 70.0}
        self.DEFAULT_SNIPER_MULTIPLIER = {"SOXL": 1.0, "TQQQ": 0.9}
        self.DEFAULT_FEE = {"SOXL": 0.25, "TQQQ": 0.25} # NEW: 기본 수수료 0.25%
        
        self._escrow_cache = {}
        self._locks_mutex = threading.Lock()

    def _atomic_update_locks(self, update_fn):
        with self._locks_mutex:
            lock_file_path = self.FILES["LOCKS"]
            dir_name = os.path.dirname(lock_file_path) or '.'
            if not os.path.exists(dir_name):
                os.makedirs(dir_name, exist_ok=True)
                
            sentinel = lock_file_path + ".lock"
            with open(sentinel, 'w') as lf:
                if fcntl:
                    fcntl.flock(lf, fcntl.LOCK_EX)
                try:
                    locks = self._load_json(lock_file_path, {})
                    update_fn(locks)
                    self._save_json(lock_file_path, locks)
                finally:
                    if fcntl:
                        fcntl.flock(lf, fcntl.LOCK_UN)

    def _load_json(self, filename, default=None):
        if os.path.exists(filename):
            try:
                with open(filename, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception as e:
                print(f"⚠️ [Config] JSON 로드 에러 ({filename}): {e}")
                try:
                    shutil.copy(filename, filename + f".bak_{int(time.time())}")
                except Exception as backup_e:
                    print(f"⚠️ [Config] 백업 실패: {backup_e}")
                return default if default is not None else {}
        return default if default is not None else {}

    def _save_json(self, filename, data):
        fd = None
        temp_path = None
        try:
            dir_name = os.path.dirname(filename) or '.'
            if not os.path.exists(dir_name):
                os.makedirs(dir_name, exist_ok=True)
                
            fd, temp_path = tempfile.mkstemp(dir=dir_name, text=True)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                fd = None
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()         
                os.fsync(f.fileno()) 
                
            os.replace(temp_path, filename)
            temp_path = None
        except Exception as e:
            print(f"❌ [Config] JSON 저장 중 치명적 에러 발생 ({filename}): {e}")
            if fd is not None:
                try: os.close(fd)
                except OSError: pass
            if temp_path and os.path.exists(temp_path):
                try: os.remove(temp_path)
                except Exception: pass

    def _load_file(self, filename, default=None):
        if os.path.exists(filename):
            try:
                with open(filename, 'r', encoding='utf-8') as f:
                    return f.read().strip()
            except Exception as e:
                print(f"⚠️ [Config] 파일 로드 에러 ({filename}): {e}")
        return default

    def _save_file(self, filename, content):
        fd = None
        temp_path = None
        try:
            dir_name = os.path.dirname(filename) or '.'
            if not os.path.exists(dir_name):
                os.makedirs(dir_name, exist_ok=True)
                
            fd, temp_path = tempfile.mkstemp(dir=dir_name, text=True)
            with os.fdopen(fd, 'w', encoding='utf-8') as f:
                fd = None
                f.write(str(content))
                f.flush()
                os.fsync(f.fileno()) 
            os.replace(temp_path, filename)
            temp_path = None
        except Exception as e:
            print(f"❌ [Config] 텍스트 파일 저장 에러 ({filename}): {e}")
            if fd is not None:
                try: os.close(fd)
                except OSError: pass
            if temp_path and os.path.exists(temp_path):
                try: os.remove(temp_path)
                except Exception: pass

    def get_last_split_date(self, ticker):
        return self._load_json(self.FILES["SPLIT_HISTORY"], {}).get(ticker, "")

    def set_last_split_date(self, ticker, date_str):
        d = self._load_json(self.FILES["SPLIT_HISTORY"], {})
        d[ticker] = date_str
        self._save_json(self.FILES["SPLIT_HISTORY"], d)

    def get_ledger(self):
        return self._load_json(self.FILES["LEDGER"], [])

    def get_escrow_cash(self, ticker):
        locks = self._load_json(self.FILES["LOCKS"], {})
        persistent_escrow = locks.get(f"ESCROW_{ticker}", None)
        
        if persistent_escrow is not None:
            return max(0.0, float(persistent_escrow))

        ledger = self.get_ledger()
        escrow = 0.0
        for r in reversed(ledger):
            if r.get('ticker') == ticker:
                if r.get('is_reverse', False):
                    if r['side'] == 'SELL':
                        escrow += (r['qty'] * r['price'])
                    elif r['side'] == 'BUY':
                        escrow -= (r['qty'] * r['price'])
                else:
                    break
        return max(0.0, float(escrow))

    def set_escrow_cash(self, ticker, amount):
        validated = max(0.0, float(amount))
        def _update(locks):
            locks[f"ESCROW_{ticker}"] = validated
        self._atomic_update_locks(_update)

    def add_escrow_cash(self, ticker, amount):
        def _update(locks):
            current = locks.get(f"ESCROW_{ticker}", 0.0)
            locks[f"ESCROW_{ticker}"] = max(0.0, current + float(amount))
        self._atomic_update_locks(_update)

    def clear_escrow_cash(self, ticker):
        def _update(locks):
            if f"ESCROW_{ticker}" in locks:
                del locks[f"ESCROW_{ticker}"]
        self._atomic_update_locks(_update)

    def get_total_locked_cash(self, exclude_ticker=None):
        total = 0.0
        try:
            tickers = self.get_active_tickers()
            for t in tickers:
                if t != exclude_ticker:
                    rev_state = self.get_reverse_state(t).get("is_active", False)
                    if rev_state:
                        total += self.get_escrow_cash(t)
        except Exception:
            pass
        return total

    def get_order_locked(self, ticker):
        locks = self._load_json(self.FILES["LOCKS"], {})
        return locks.get(f"ORDER_LOCKED_{ticker}", False)

    def set_order_locked(self, ticker, is_locked):
        def _update(locks):
            if is_locked:
                locks[f"ORDER_LOCKED_{ticker}"] = True
            else:
                if f"ORDER_LOCKED_{ticker}" in locks:
                    del locks[f"ORDER_LOCKED_{ticker}"]
        self._atomic_update_locks(_update)

    def set_lock(self, ticker, market_type):
        est = pytz.timezone('US/Eastern')
        today = datetime.datetime.now(est).strftime('%Y-%m-%d')
        def _update(locks):
            locks[f"{today}_{ticker}_{market_type}"] = True
        self._atomic_update_locks(_update)

    def reset_locks(self):
        def _update(locks):
            keys_to_keep = [k for k in locks.keys() if k.startswith("ESCROW_") or k.startswith("ORDER_LOCKED_")]
            surviving_locks = {k: locks[k] for k in keys_to_keep}
            locks.clear()
            locks.update(surviving_locks)
        self._atomic_update_locks(_update)
        
    def reset_lock_for_ticker(self, ticker):
        est = pytz.timezone('US/Eastern')
        today = datetime.datetime.now(est).strftime('%Y-%m-%d')
        def _update(locks):
            keys_to_delete = [k for k in locks.keys() if k.startswith(f"{today}_{ticker}")]
            for k in keys_to_delete:
                del locks[k]
        self._atomic_update_locks(_update)

    def check_lock(self, ticker, market_type):
        est = pytz.timezone('US/Eastern')
        today = datetime.datetime.now(est).strftime('%Y-%m-%d')
        locks = self._load_json(self.FILES["LOCKS"], {})
        return locks.get(f"{today}_{ticker}_{market_type}", False)

    def get_absolute_t_val(self, ticker, actual_qty, actual_avg_price):
        seed = self.get_seed(ticker)
        split = self.get_split_count(ticker)
        one_portion = seed / split if split > 0 else 1
        t_val = (actual_qty * actual_avg_price) / one_portion if one_portion > 0 else 0.0
        return round(t_val, 4), one_portion

    def apply_stock_split(self, ticker, ratio):
        if ratio <= 0: return
        ledger = self.get_ledger()
        changed = False
        for r in ledger:
            if r.get('ticker') == ticker:
                raw_new_qty = r['qty'] * ratio
                new_qty = math.floor(raw_new_qty + 0.5)
                r['qty'] = new_qty if new_qty > 0 else (1 if r['qty'] > 0 else 0)
                r['price'] = round(r['price'] / ratio, 4)
                if 'avg_price' in r:
                    r['avg_price'] = round(r['avg_price'] / ratio, 4)
                changed = True
        if changed:
            self._save_json(self.FILES["LEDGER"], ledger)

    def overwrite_genesis_ledger(self, ticker, genesis_records, actual_avg):
        ledger = self.get_ledger()
        target_recs = [r for r in ledger if r['ticker'] == ticker]
        
        if len(target_recs) > 0:
            print(f"⚠️ [보안 차단] {ticker}의 장부 기록이 이미 존재하여 파괴적 Genesis 덮어쓰기를 차단했습니다.")
            return

        max_id = max([r.get('id', 0) for r in ledger] + [0])
        for i, rec in enumerate(genesis_records):
            max_id += 1
            ledger.append({
                "id": max_id,
                "date": rec['date'],
                "ticker": ticker,
                "side": rec['side'],
                "price": rec['price'],
                "qty": rec['qty'],
                "avg_price": actual_avg, 
                "exec_id": f"GENESIS_{int(time.time())}_{i}",
                "desc": "✨과거기록복원",
                "is_reverse": False 
            })
        self._save_json(self.FILES["LEDGER"], ledger)

    def overwrite_incremental_ledger(self, ticker, temp_recs, new_today_records):
        ledger = self.get_ledger()
        remaining = [r for r in ledger if r['ticker'] != ticker]
        updated_ticker_recs = list(temp_recs)
        
        current_rev_state = self.get_reverse_state(ticker).get("is_active", False)
        max_id = max([r.get('id', 0) for r in ledger] + [0])
        
        for i, rec in enumerate(new_today_records):
            max_id += 1
            new_row = {
                "id": max_id,
                "date": rec['date'],
                "ticker": ticker,
                "side": rec['side'],
                "price": rec['price'],
                "qty": rec['qty'],
                "avg_price": rec['avg_price'],
                "exec_id": rec.get("exec_id", f"FASTTRACK_{int(time.time())}_{i}"),
                "is_reverse": current_rev_state
            }
            if "desc" in rec:
                new_row["desc"] = rec["desc"]
                
            updated_ticker_recs.append(new_row)
            
        remaining.extend(updated_ticker_recs)
        self._save_json(self.FILES["LEDGER"], remaining)

    def overwrite_ledger(self, ticker, actual_qty, actual_avg):
        ledger = self.get_ledger()
        target_recs = [r for r in ledger if r['ticker'] == ticker]
        
        if len(target_recs) > 0:
            print(f"⚠️ [보안 차단] {ticker}의 장부 기록이 이미 존재하여 파괴적 INIT 덮어쓰기를 차단했습니다.")
            return
            
        est = pytz.timezone('US/Eastern')
        today_str = datetime.datetime.now(est).strftime('%Y-%m-%d')
        new_id = 1 if not ledger else max(r.get('id', 0) for r in ledger) + 1
        
        ledger.append({
            "id": new_id, "date": today_str, "ticker": ticker, "side": "BUY",
            "price": actual_avg, "qty": actual_qty, "avg_price": actual_avg, 
            "exec_id": f"INIT_{int(time.time())}", "desc": "✨최초스냅샷", "is_reverse": False
        })
        self._save_json(self.FILES["LEDGER"], ledger)

    def calibrate_avg_price(self, ticker, actual_avg):
        ledger = self.get_ledger()
        target_recs = [r for r in ledger if r['ticker'] == ticker]
        if target_recs:
            for r in target_recs:
                r['avg_price'] = actual_avg
            self._save_json(self.FILES["LEDGER"], ledger)

    def calibrate_ledger_prices(self, ticker, target_date_str, exec_history):
        if not exec_history:
            return 0
            
        buy_qty = 0
        buy_amt = 0.0
        sell_qty = 0
        sell_amt = 0.0
        
        for ex in exec_history:
            side_cd = ex.get('sll_buy_dvsn_cd')
            qty = int(float(ex.get('ft_ccld_qty', '0')))
            price = float(ex.get('ft_ccld_unpr3', '0'))
            
            if qty > 0 and price > 0:
                if side_cd == "02": 
                    buy_qty += qty
                    buy_amt += (qty * price)
                elif side_cd == "01": 
                    sell_qty += qty
                    sell_amt += (qty * price)
                    
        actual_buy_price = round(buy_amt / buy_qty, 4) if buy_qty > 0 else 0.0
        actual_sell_price = round(sell_amt / sell_qty, 4) if sell_qty > 0 else 0.0
        
        if actual_buy_price == 0.0 and actual_sell_price == 0.0:
            return 0
            
        ledger = self.get_ledger()
        changed_count = 0
        
        for r in ledger:
            if r.get('ticker') == ticker and r.get('date') == target_date_str:
                exec_id = str(r.get('exec_id', ''))
                if 'INIT' in exec_id:
                    continue
                    
                if r['side'] == 'BUY' and actual_buy_price > 0.0:
                    if abs(r['price'] - actual_buy_price) >= 0.01:
                        r['price'] = actual_buy_price
                        changed_count += 1
                elif r['side'] == 'SELL' and actual_sell_price > 0.0:
                    if abs(r['price'] - actual_sell_price) >= 0.01:
                        r['price'] = actual_sell_price
                        changed_count += 1
                        
        if changed_count > 0:
            self._save_json(self.FILES["LEDGER"], ledger)
            
        return changed_count

    def clear_ledger_for_ticker(self, ticker):
        ledger = self.get_ledger()
        remaining = [r for r in ledger if r['ticker'] != ticker]
        self._save_json(self.FILES["LEDGER"], remaining)
        self.set_reverse_state(ticker, False, 0, 0.0)
        self.clear_escrow_cash(ticker)

    def calculate_holdings(self, ticker, records=None):
        if records is None:
            records = self.get_ledger()
        target_recs = [r for r in records if r['ticker'] == ticker]
        total_qty, total_invested, total_sold = 0, 0.0, 0.0    
        
        running_qty = 0
        running_cost = 0.0

        for r in target_recs:
            if r['side'] == 'BUY':
                total_qty += r['qty']
                total_invested += (r['price'] * r['qty'])
                running_qty += r['qty']
                running_cost += (r['price'] * r['qty'])
            elif r['side'] == 'SELL':
                total_qty -= r['qty']
                total_sold += (r['price'] * r['qty'])
                if running_qty > 0:
                    cost_per_share = running_cost / running_qty
                    running_cost -= cost_per_share * min(r['qty'], running_qty)
                    running_qty = max(0, running_qty - r['qty'])
        
        total_qty = max(0, int(total_qty))
        invested_up = math.ceil(total_invested * 100) / 100.0
        sold_up = math.ceil(total_sold * 100) / 100.0
        
        avg_price = 0.0
        if total_qty > 0 and target_recs:
            avg_price = float(target_recs[-1].get('avg_price', 0.0))
            if avg_price == 0.0:
                avg_price = (running_cost / running_qty) if running_qty > 0 else 0.0
        
        return total_qty, avg_price, invested_up, sold_up

    def get_reverse_state(self, ticker):
        d = self._load_json(self.FILES["REVERSE_CFG"], {})
        return d.get(ticker, {"is_active": False, "day_count": 0, "exit_target": 0.0, "last_update_date": ""})

    def set_reverse_state(self, ticker, is_active, day_count, exit_target=0.0, last_update_date=None):
        if last_update_date is None:
            est = pytz.timezone('US/Eastern')
            last_update_date = datetime.datetime.now(est).strftime('%Y-%m-%d')
            
        d = self._load_json(self.FILES["REVERSE_CFG"], {})
        d[ticker] = {"is_active": is_active, "day_count": day_count, "exit_target": exit_target, "last_update_date": last_update_date}
        self._save_json(self.FILES["REVERSE_CFG"], d)

    def update_reverse_day_if_needed(self, ticker):
        pass

    def increment_reverse_day(self, ticker):
        state = self.get_reverse_state(ticker)
        if state.get("is_active"):
            est = pytz.timezone('US/Eastern')
            now_est = datetime.datetime.now(est)
            today_est_str = now_est.strftime('%Y-%m-%d')
            
            if state.get("last_update_date") != today_est_str:
                is_trading_day = False
                try:
                    nyse = mcal.get_calendar('NYSE')
                    schedule = nyse.schedule(start_date=now_est.date(), end_date=now_est.date())
                    is_trading_day = not schedule.empty
                except Exception as e:
                    print(f"⚠️ [Config] 달력 라이브러리 에러 발생. 평일 강제 개장 처리합니다: {e}")
                    is_trading_day = now_est.weekday() < 5
                
                if is_trading_day:
                    new_day = state.get("day_count", 0) + 1
                    self.set_reverse_state(ticker, True, new_day, state.get("exit_target", 0.0), today_est_str)
                    return True
                else:
                    self.set_reverse_state(ticker, True, state.get("day_count", 0), state.get("exit_target", 0.0), today_est_str)
                    return False
        return False

    def calculate_v14_state(self, ticker):
        ledger = self.get_ledger()
        target_recs = sorted([r for r in ledger if r['ticker'] == ticker], key=lambda x: x.get('id', 0))

        seed = self.get_seed(ticker)
        split = self.get_split_count(ticker)
        base_portion = seed / split if split > 0 else 1
        fee_rate = self.get_fee(ticker) / 100.0  # NEW: 왕복 수수료 반영 — 잔액/예산 계산이 실제 체결 원가와 일치하도록

        holdings = 0
        rem_cash = seed
        total_invested = 0.0

        for r in target_recs:
            if holdings == 0:
                rem_cash = seed
                total_invested = 0.0

            qty = r['qty']
            amt = qty * r['price']

            if r['side'] == 'BUY':
                net_amt = amt * (1.0 + fee_rate)
                rem_cash -= net_amt
                holdings += qty
                total_invested += net_amt

            elif r['side'] == 'SELL':
                net_amt = amt * (1.0 - fee_rate)
                if qty >= holdings:
                    holdings = 0
                    rem_cash = seed
                    total_invested = 0.0
                else:
                    if holdings > 0:
                        avg_price = total_invested / holdings
                        total_invested -= (qty * avg_price)
                    holdings -= qty
                    rem_cash += net_amt
                    
        avg_price = total_invested / holdings if holdings > 0 else 0.0
        t_val = (holdings * avg_price) / base_portion if base_portion > 0 else 0.0
            
        if holdings > 0:
            safe_denom = max(1.0, split - t_val)
            current_budget = rem_cash / safe_denom
        else:
            current_budget = base_portion
            t_val = 0.0
            
        return max(0.0, round(t_val, 4)), max(0.0, current_budget), max(0.0, rem_cash)

    def archive_graduation(self, ticker, end_date, prev_close=0.0):
        ledger = self.get_ledger()
        target_recs = [r for r in ledger if r['ticker'] == ticker]
        if not target_recs:
            return None, 0
        
        ledger_qty, avg_price, _, _ = self.calculate_holdings(ticker, target_recs)

        if ledger_qty > 0:
            split = self.get_split_count(ticker)
            is_reverse = self.get_reverse_state(ticker).get("is_active", False)

            if is_reverse:
                divisor = 10 if split <= 20 else 20
                loc_qty = math.floor(ledger_qty / divisor)
            else:
                loc_qty = math.ceil(ledger_qty / 4)

            limit_qty = ledger_qty - loc_qty
            if limit_qty < 0: 
                loc_qty = ledger_qty
                limit_qty = 0

            target_ratio = self.get_target_profit(ticker) / 100.0
            target_price = math.ceil(avg_price * (1 + target_ratio) * 100) / 100.0
            loc_price = prev_close if prev_close > 0 else avg_price

            new_id = max((r.get('id', 0) for r in ledger), default=0) + 1

            if loc_qty > 0:
                rec_loc = {"id": new_id, "date": end_date, "ticker": ticker, "side": "SELL", "price": loc_price, "qty": loc_qty, "avg_price": avg_price, "exec_id": f"GRAD_LOC_{int(time.time())}", "is_reverse": is_reverse}
                ledger.append(rec_loc)
                target_recs.append(rec_loc)
                new_id += 1

            if limit_qty > 0:
                rec_limit = {"id": new_id, "date": end_date, "ticker": ticker, "side": "SELL", "price": target_price, "qty": limit_qty, "avg_price": avg_price, "exec_id": f"GRAD_LMT_{int(time.time())}", "is_reverse": is_reverse}
                ledger.append(rec_limit)
                target_recs.append(rec_limit)

            self._save_json(self.FILES["LEDGER"], ledger)

        # 졸업 SELL 레코드 추가 후 집계 (이전에 계산하면 졸업 매도금액 누락)
        raw_total_buy = sum(r['price']*r['qty'] for r in target_recs if r['side']=='BUY')
        raw_total_sell = sum(r['price']*r['qty'] for r in target_recs if r['side']=='SELL')

        fee_rate = self.get_fee(ticker) / 100.0
        net_invested = raw_total_buy * (1.0 + fee_rate)
        net_revenue = raw_total_sell * (1.0 - fee_rate)
        
        profit = math.ceil((net_revenue - net_invested) * 100) / 100.0
        yield_pct = math.ceil(((profit / net_invested * 100) if net_invested > 0 else 0.0) * 100) / 100.0
        
        compound_rate = self.get_compound_rate(ticker) / 100.0
        added_seed = 0
        if profit > 0 and compound_rate > 0:
            added_seed = math.floor(profit * compound_rate)
            current_seed = self.get_seed(ticker)
            self.set_seed(ticker, current_seed + added_seed)

        history = self._load_json(self.FILES["HISTORY"], [])
        new_hist = {
            "id": len(history) + 1, "ticker": ticker, "end_date": end_date,
            "profit": profit, "yield": yield_pct, "revenue": net_revenue, "invested": net_invested, "trades": target_recs
        }
        history.append(new_hist)
        self._save_json(self.FILES["HISTORY"], history)
        
        self.clear_ledger_for_ticker(ticker)
        
        return new_hist, added_seed

    def get_full_version_history(self):
        return VERSION_HISTORY

    def get_version_history(self):
        return VERSION_HISTORY

    def get_latest_version(self):
        history = self.get_version_history()
        if history and len(history) > 0:
            latest_entry = history[-1]
            if isinstance(latest_entry, dict):
                return latest_entry.get("version", "V14.x")
            elif isinstance(latest_entry, str):
                return latest_entry.split(' ')[0] 
        return "V14.x"

    def get_history(self):
        return self._load_json(self.FILES["HISTORY"], [])

    def get_seed(self, t): return float(self._load_json(self.FILES["SEED_CFG"], self.DEFAULT_SEED).get(t, 6720.0))
    def set_seed(self, t, v): 
        d = self._load_json(self.FILES["SEED_CFG"], self.DEFAULT_SEED)
        d[t] = v
        self._save_json(self.FILES["SEED_CFG"], d)

    def get_compound_rate(self, t): return float(self._load_json(self.FILES["COMPOUND_CFG"], self.DEFAULT_COMPOUND).get(t, 70.0))
    def set_compound_rate(self, t, v):
        d = self._load_json(self.FILES["COMPOUND_CFG"], self.DEFAULT_COMPOUND)
        d[t] = v
        self._save_json(self.FILES["COMPOUND_CFG"], d)

    def get_version(self, t): return self._load_json(self.FILES["VERSION_CFG"], self.DEFAULT_VERSION).get(t, "V14")
    def set_version(self, t, v):
        d = self._load_json(self.FILES["VERSION_CFG"], self.DEFAULT_VERSION)
        d[t] = v
        self._save_json(self.FILES["VERSION_CFG"], d)

    def get_split_count(self, t): return self._load_json(self.FILES["SPLIT"], self.DEFAULT_SPLIT).get(t, 40.0)
    def get_target_profit(self, t): return self._load_json(self.FILES["PROFIT_CFG"], self.DEFAULT_TARGET).get(t, 10.0)
        
    def get_fee(self, t): 
        return float(self._load_json(self.FILES["FEE_CFG"], self.DEFAULT_FEE).get(t, 0.25))
    def set_fee(self, t, v):
        d = self._load_json(self.FILES["FEE_CFG"], self.DEFAULT_FEE)
        d[t] = float(v)
        self._save_json(self.FILES["FEE_CFG"], d)

    def get_sniper_multiplier(self, t):
        default_val = self.DEFAULT_SNIPER_MULTIPLIER.get(t, 1.0)
        return float(self._load_json(self.FILES["SNIPER_MULTIPLIER_CFG"], self.DEFAULT_SNIPER_MULTIPLIER).get(t, default_val))
        
    def set_sniper_multiplier(self, t, v):
        d = self._load_json(self.FILES["SNIPER_MULTIPLIER_CFG"], self.DEFAULT_SNIPER_MULTIPLIER)
        d[t] = float(v)
        self._save_json(self.FILES["SNIPER_MULTIPLIER_CFG"], d)

    def get_upward_sniper_mode(self, ticker): return self._load_json(self.FILES["UPWARD_SNIPER"], {}).get(ticker, False)
    def set_upward_sniper_mode(self, ticker, v):
        d = self._load_json(self.FILES["UPWARD_SNIPER"], {})
        d[ticker] = bool(v)
        self._save_json(self.FILES["UPWARD_SNIPER"], d)

    def get_avwap_hybrid_mode(self, ticker): return self._load_json(self.FILES["AVWAP_HYBRID_CFG"], {}).get(ticker, False)
    def set_avwap_hybrid_mode(self, ticker, v):
        d = self._load_json(self.FILES["AVWAP_HYBRID_CFG"], {})
        d[ticker] = bool(v)
        self._save_json(self.FILES["AVWAP_HYBRID_CFG"], d)

    def get_manual_vwap_mode(self, ticker): return self._load_json(self.FILES["MANUAL_VWAP_CFG"], {}).get(ticker, False)
    def set_manual_vwap_mode(self, ticker, v):
        d = self._load_json(self.FILES["MANUAL_VWAP_CFG"], {})
        d[ticker] = bool(v)
        self._save_json(self.FILES["MANUAL_VWAP_CFG"], d)

    # ==========================================================
    # 🚨 [V28.50 NEW] AVWAP 암살자 조기 퇴근 듀얼 코어 Getter/Setter
    # ==========================================================
    def get_avwap_early_exit_mode(self, ticker): 
        return self._load_json(self.FILES["AVWAP_EARLY_EXIT_CFG"], {}).get(ticker, False)
        
    def set_avwap_early_exit_mode(self, ticker, v):
        d = self._load_json(self.FILES["AVWAP_EARLY_EXIT_CFG"], {})
        d[ticker] = bool(v)
        self._save_json(self.FILES["AVWAP_EARLY_EXIT_CFG"], d)

    def get_avwap_early_target(self, ticker): 
        # 기본값 2.5(%) 로 설정
        return float(self._load_json(self.FILES["AVWAP_EARLY_TARGET_CFG"], {}).get(ticker, 2.5))
        
    def set_avwap_early_target(self, ticker, v):
        d = self._load_json(self.FILES["AVWAP_EARLY_TARGET_CFG"], {})
        d[ticker] = float(v)
        self._save_json(self.FILES["AVWAP_EARLY_TARGET_CFG"], d)
    # ==========================================================

    # ==========================================================
    # NEW: VR5 밸류리밸런싱 설정 Getter/Setter
    # ==========================================================
    def get_vr_config(self, ticker):
        """VR5 설정 조회. 없으면 기본값 반환."""
        defaults = {
            "enabled": False,
            "v_value": 0.0,
            "pool": 0.0,
            "pool_initial": 0.0,  # NEW: Pool 잔액 역산의 기준점(처음 pool). 최초 1회만 설정, 이후 불변.
            "g_factor": 10,
            "band_pct": 15,
            "last_v_update": "",
            "v_update_weeks": 2,
            "mode": "ACCUM",
            "deposit": 0.0,   # 정기 적립금 (V 업데이트 시 자동 반영)
            "start_date": "",  # VR 최초 시작일자
            "dividends": 0.0,  # NEW: 누적 배당금 (Pool 잔액 계산에 가산)
            "last_g_update": "",  # NEW: G계수 마지막 자동 증가일자 (미설정 시 start_date로 폴백)
            "g_update_months": 6,  # NEW: G계수 자동 증가 주기(개월)
            "v_initial": 0.0,  # NEW: 최초 V값(투자원금 계산의 기준점). 최초 1회만 설정, 이후 불변.
            "invested_deposits": 0.0,  # NEW: V 업데이트 때마다 반영된 정기 적립금/추가 입출금의 누적합(투자원금 계산용)
        }
        saved = self._load_json(self.FILES["VR_CFG"], {}).get(ticker, {})
        cfg = {**defaults, **saved}
        # 🚨 [Pool 마이그레이션] pool_initial 미설정 상태에서 pool 값이 있으면, 그 값을
        # "처음 pool"(기준점)로 승격시킨다. 과거 매매 이력은 소급 반영할 수 없으므로
        # 이 시점 이후의 KIS 체결내역만으로 Pool 잔액이 역산된다.
        if cfg["pool_initial"] <= 0 and cfg["pool"] > 0:
            cfg["pool_initial"] = cfg["pool"]
        # 🚨 [투자금 마이그레이션] v_initial 미설정 상태(이 필드 도입 이전에 이미 초기 설정된
        # 티커)에서 v_value가 있으면, 그 값을 "최초 V"(투자원금 계산 기준점)로 근사 승격시킨다.
        if cfg["v_initial"] <= 0 and cfg["v_value"] > 0:
            cfg["v_initial"] = cfg["v_value"]
        return cfg

    def set_vr_config(self, ticker, cfg_data):
        """VR5 설정 저장."""
        d = self._load_json(self.FILES["VR_CFG"], {})
        d[ticker] = cfg_data
        self._save_json(self.FILES["VR_CFG"], d)

    def get_vr_tickers(self):
        """VR5 활성화된 티커 목록 반환."""
        d = self._load_json(self.FILES["VR_CFG"], {})
        return [t for t, v in d.items() if v.get('enabled')]

    def set_vr_pending_orders(self, ticker, date_str, orders):
        """VR5 사다리 주문 승인 대기열 저장. date_str(EST)이 다른 날이면 실행 시 무효 처리됨."""
        d = self._load_json(self.FILES["VR_PENDING"], {})
        d[ticker] = {"date": date_str, "orders": orders}
        self._save_json(self.FILES["VR_PENDING"], d)

    def pop_vr_pending_orders(self, ticker, date_str):
        """당일 대기열만 꺼내서 반환(반환 즉시 삭제 — 중복 승인 방지). 날짜 불일치 시 None."""
        d = self._load_json(self.FILES["VR_PENDING"], {})
        entry = d.get(ticker)
        if not entry or entry.get("date") != date_str:
            return None
        del d[ticker]
        self._save_json(self.FILES["VR_PENDING"], d)
        return entry.get("orders", [])

    def get_vr_ledger(self, ticker):
        """VR5 체결 원장 조회. KIS 체결내역 동기화(sync_vr_ledger)로만 채워짐."""
        d = self._load_json(self.FILES["VR_LEDGER"], {})
        return d.get(ticker, {"records": [], "last_synced": ""})

    def sync_vr_ledger(self, ticker, date_str, execs):
        """지정일(EST, YYYY-MM-DD) KIS 체결내역을 VR 원장에 1회만 반영(멱등).
        같은 date_str로 이미 동기화됐으면 스킵하고 0을 반환."""
        d = self._load_json(self.FILES["VR_LEDGER"], {})
        entry = d.get(ticker, {"records": [], "last_synced": ""})
        if entry.get("last_synced") == date_str:
            return 0
        added = 0
        for ex in (execs or []):
            try:
                qty = int(float(ex.get('ft_ccld_qty', '0') or 0))
                price = float(ex.get('ft_ccld_unpr3', '0') or 0)
            except (TypeError, ValueError):
                continue
            if qty <= 0 or price <= 0:
                continue
            side = "BUY" if ex.get('sll_buy_dvsn_cd') == "02" else "SELL"
            entry["records"].append({"date": date_str, "side": side, "qty": qty, "price": round(price, 4)})
            added += 1
        entry["last_synced"] = date_str
        d[ticker] = entry
        self._save_json(self.FILES["VR_LEDGER"], d)
        return added

    def get_vr_pool_state(self, ticker):
        """Pool 잔액 역산: Pool_current = 처음pool(pool_initial) + 누적 적립금(invested_deposits) - (총매수금액 - 총매도금액) + 배당금
        정기 적립금은 V 업데이트 시 실제 계좌에 새 현금으로 들어와 Pool(매매 재원)에 합산되고, 이후 매매(net_trade)로 소진/회수된다.
        반환: (pool_current, total_buy, total_sell, net_trade)"""
        vr_cfg = self.get_vr_config(ticker)
        pool_initial = float(vr_cfg.get('pool_initial', 0.0))
        dividends = float(vr_cfg.get('dividends', 0.0))
        deposits = float(vr_cfg.get('invested_deposits', 0.0))  # NEW: 누적 적립/입출금(Pool 재원으로 합산)
        records = self.get_vr_ledger(ticker).get('records', [])
        total_buy = sum(r['qty'] * r['price'] for r in records if r['side'] == 'BUY')
        total_sell = sum(r['qty'] * r['price'] for r in records if r['side'] == 'SELL')
        net_trade = total_buy - total_sell
        pool_current = pool_initial + deposits - net_trade + dividends
        return round(pool_current, 2), round(total_buy, 2), round(total_sell, 2), round(net_trade, 2)

    def get_vr_investment_summary(self, ticker, current_value):
        """VR5 '누적 정리' 리포트용 요약 계산 (2주마다 자동 발송).
        투자금 = 최초 V값(v_initial) + 처음 Pool(pool_initial) + 누적 적립/출금액(invested_deposits)
        """
        vr_cfg = self.get_vr_config(ticker)
        pool_current, _, _, _ = self.get_vr_pool_state(ticker)
        invested = (
            float(vr_cfg.get('v_initial', 0.0))
            + float(vr_cfg.get('pool_initial', 0.0))
            + float(vr_cfg.get('invested_deposits', 0.0))
        )
        account_total = current_value + pool_current
        profit = account_total - invested
        yield_pct = (profit / invested * 100) if invested > 0 else 0.0
        multiple = (account_total / invested) if invested > 0 else 0.0
        return {
            "current_value": round(current_value, 2),
            "pool": round(pool_current, 2),
            "account_total": round(account_total, 2),
            "invested": round(invested, 2),
            "profit": round(profit, 2),
            "yield_pct": round(yield_pct, 2),
            "multiple": round(multiple, 2),
        }
    # ==========================================================

    def get_secret_mode(self): return self._load_file(self.FILES["SECRET_MODE"]) == 'True'
    def set_secret_mode(self, v): self._save_file(self.FILES["SECRET_MODE"], str(v))
    def get_active_tickers(self): return self._load_json(self.FILES["TICKER"], ["SOXL", "TQQQ"])
    def set_active_tickers(self, v): self._save_json(self.FILES["TICKER"], v)
    def get_chat_id(self): 
        v = self._load_file(self.FILES["CHAT_ID"])
        return int(v) if v else None
    def set_chat_id(self, v): self._save_file(self.FILES["CHAT_ID"], v)
