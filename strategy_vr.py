# ==========================================================
# [strategy_vr.py] - VR5 밸류리밸런싱 전략 엔진
# NEW: 라오어 밸류리밸런싱(Value Rebalancing) 투자법 구현
# - V: 목표 포트폴리오 가치
# - 밴드: V×(1±band_pct%) 범위 이탈 시 매수/매도
# - V 업데이트 주기: 2주마다 (V2 = V1 + Pool/G + (E-V1)/(2*sqrt(G)) + deposit)
# - G: 분할 계수 (적립식=10)
# - 주문 방식: 지정가(LIMIT)
# ==========================================================
import datetime
import logging
import math


class VRStrategy:
    """VR5 밸류리밸런싱 전략 엔진 (V14/V-REV와 완전 독립)"""

    def _calc_lot_size(self, current_qty, low, high, range_low, range_high, max_orders_per_side):
        """포트폴리오 규모(보유수량) 비례 사다리 묶음 단위(lot) 역산.

        1주 단위 사다리는 보유수량이 커질수록 1주당 가격 변화폭이 미세해져
        max_orders_per_side 칸으로는 목표 범위(range_low~range_high)를 다 못
        덮는다. 정확히 max_orders_per_side 칸이 그 범위를 커버하도록 lot 크기를
        역산하면, 계좌 규모와 무관하게 항상 일정한 주문 건수로 일일 변동폭을
        커버할 수 있다 (라오어 원전 슬라이드의 "8개씩" 묶음매수와 동일한 발상).
        """
        rungs = max(1, max_orders_per_side - 1)
        buy_lot = 0.0
        if low > 0 and range_low > 0:
            target_prev_qty = low / range_low
            buy_lot = max(0.0, (target_prev_qty - current_qty) / rungs)
        sell_lot = 0.0
        if high > 0 and range_high > 0 and current_qty > 0:
            target_prev_qty = high / range_high
            sell_lot = max(0.0, (current_qty - target_prev_qty) / rungs)
        return max(1, round(max(buy_lot, sell_lot)))

    def get_ladder_orders(self, ticker, current_price, current_qty, vr_cfg, range_pct=15.0, max_orders_per_side=20):
        """
        라오어 VR 원전 방식: 밴드 경계 터치 가격에 lot 단위 사다리 주문 생성
        - m번째 매수가 = V×(1-밴드) / (보유수량+(m-1)×lot)  → lot주 살 때마다 다음 매수가 하락
        - m번째 매도가 = V×(1+밴드) / (보유수량-(m-1)×lot)  → lot주 팔 때마다 다음 매도가 상승
        - lot(주문 묶음 단위)은 포트폴리오 규모에 비례해 자동 산출됨(_calc_lot_size) —
          계좌가 작으면 1주씩, 커지면 여러 주씩 묶여서 나간다.
        - 현재가 ±range_pct% 범위 내의 주문만 반환 (당일 유효 주문이므로 하루 변동폭만 커버)

        Returns:
            dict with keys: orders(list of {side, qty, price}), v, low, high,
                            range_low, range_high, portfolio, lot_size, reason
        """
        base = {'orders': [], 'v': 0.0, 'low': 0.0, 'high': 0.0,
                'range_low': 0.0, 'range_high': 0.0, 'portfolio': 0.0, 'lot_size': 1}

        if not vr_cfg.get('enabled'):
            return {**base, 'reason': 'VR 비활성화'}
        v = float(vr_cfg.get('v_value', 0))
        if v <= 0:
            return {**base, 'reason': 'V 값 미설정'}
        if current_price <= 0:
            return {**base, 'reason': '현재가 조회 실패'}
        if current_qty <= 0:
            return {**base, 'reason': '보유수량 0 — 초기 매수 후 사다리 생성 가능'}

        band_pct = float(vr_cfg.get('band_pct', 15)) / 100.0
        low = v * (1 - band_pct)
        high = v * (1 + band_pct)
        range_low = current_price * (1 - range_pct / 100.0)
        range_high = current_price * (1 + range_pct / 100.0)

        lot_size = self._calc_lot_size(current_qty, low, high, range_low, range_high, max_orders_per_side)

        orders = []
        # 매수 사다리 (m이 커질수록 가격 하락). prev_qty = 이 칸 매수 "전" 보유수량 기준가.
        for m in range(1, max_orders_per_side + 1):
            prev_qty = current_qty + (m - 1) * lot_size
            if prev_qty <= 0:
                break
            price = round(low / prev_qty, 2)
            if price < range_low:
                break
            if price > range_high:
                continue  # 이미 하한 이탈 상태의 캐치업 물량 중 범위 초과분 제외
            orders.append({'side': 'BUY', 'qty': lot_size, 'price': price})

        # 매도 사다리 (m이 커질수록 가격 상승). prev_qty = 이 칸 매도 "전" 보유수량 기준가.
        # 누적 매도량이 보유수량을 넘지 않도록 마지막 칸은 잔량만큼만 매도.
        for m in range(1, max_orders_per_side + 1):
            prev_qty = current_qty - (m - 1) * lot_size
            if prev_qty <= 0:
                break
            price = round(high / prev_qty, 2)
            if price > range_high:
                break
            sell_qty = min(lot_size, prev_qty)
            if price < range_low:
                continue  # 이미 상한 이탈 상태의 캐치업 물량 중 범위 초과분 제외
            orders.append({'side': 'SELL', 'qty': sell_qty, 'price': price})

        return {
            **base,
            'orders': orders,
            'v': v,
            'low': round(low, 2),
            'high': round(high, 2),
            'range_low': round(range_low, 2),
            'range_high': round(range_high, 2),
            'portfolio': round(current_qty * current_price, 2),
            'lot_size': lot_size,
            'reason': f'사다리 {len(orders)}건 × {lot_size}주 (현재가 ±{range_pct:.0f}% 범위)',
        }

    def format_ladder_table(self, orders, current_price):
        """사다리 주문표 HTML 렌더링 (스케줄러/수동 버튼 공용)"""
        buy_lines = [o for o in orders if o['side'] == 'BUY']
        sell_lines = [o for o in orders if o['side'] == 'SELL']

        table = "\n\n━━━━━━━━━━━━━━━━━━━\n📋 <b>주문표</b> (lot 단위 × 지정가, 당일 유효)\n"
        # 매도 사다리는 높은 가격부터, 현재가 구분선 아래로 매수 사다리
        for o in sorted(sell_lines, key=lambda x: -x['price']):
            table += f"🔴 매도 {o['qty']}주 × <b>${o['price']:.2f}</b>\n"
        table += f"— 현재가 ${current_price:.2f} —\n"
        for o in sorted(buy_lines, key=lambda x: -x['price']):
            table += f"🟡 매수 {o['qty']}주 × <b>${o['price']:.2f}</b>\n"
        buy_total = sum(o['qty'] for o in buy_lines)
        sell_total = sum(o['qty'] for o in sell_lines)
        table += f"\n총 매수 {len(buy_lines)}건({buy_total}주) / 매도 {len(sell_lines)}건({sell_total}주) — 주문을 실행하시겠습니까?"
        return table

    def calc_next_v(self, vr_cfg, current_value=None, deposit=0.0):
        """
        다음 V값 계산 (라오어 원전 공식):
        V2 = V1 + Pool/G + (E - V1)/(2*sqrt(G)) + deposit
        - E(current_value): 마지막 평가금 (현재 보유수량 × 현재가). None이면 성능
          스무딩 항 없이(구 공식과 동일하게) 계산 — 가격 조회가 불가한 폴백 경로용.
        - Pool: 마지막 Pool 잔액
        - G   : 분할 계수 (적립식=10)
        - deposit: 정기 적립금 + 이번 주기 추가 입금(+)/출금(-)
        """
        v1 = float(vr_cfg.get('v_value', 0))
        pool = float(vr_cfg.get('pool', 0))
        g = int(vr_cfg.get('g_factor', 10))

        pool_term = (pool / g) if g > 0 else 0.0
        perf_term = 0.0
        if current_value is not None and g > 0:
            perf_term = (float(current_value) - v1) / (2 * math.sqrt(g))

        v2 = v1 + pool_term + perf_term + deposit
        return round(max(v2, 0), 2)

    def should_update_v(self, vr_cfg):
        """V 업데이트 주기(2주) 도달 여부 체크"""
        last_str = vr_cfg.get('last_v_update', '')
        if not last_str:
            return False
        try:
            last_dt = datetime.date.fromisoformat(last_str)
            weeks = int(vr_cfg.get('v_update_weeks', 2))
            return (datetime.date.today() - last_dt).days >= weeks * 7
        except Exception:
            return False

    def weeks_since_v_update(self, vr_cfg):
        """마지막 V 업데이트 이후 경과 주 수 (소수점 1자리)"""
        last_str = vr_cfg.get('last_v_update', '')
        if not last_str:
            return None
        try:
            last_dt = datetime.date.fromisoformat(last_str)
            return round((datetime.date.today() - last_dt).days / 7, 1)
        except Exception:
            return None

    @staticmethod
    def _add_months(d, months):
        """datetime.date에 개월수를 더한 날짜 반환 (달력월 기준, 외부 라이브러리 불필요)"""
        month_index = d.month - 1 + months
        year = d.year + month_index // 12
        month = month_index % 12 + 1
        is_leap = (year % 4 == 0 and (year % 100 != 0 or year % 400 == 0))
        days_in_month = [31, 29 if is_leap else 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31]
        day = min(d.day, days_in_month[month - 1])
        return datetime.date(year, month, day)

    def should_update_g(self, vr_cfg):
        """G계수 자동 증가 주기(기본 6개월) 도달 여부 체크. last_g_update 미설정 시 start_date로 폴백."""
        last_str = vr_cfg.get('last_g_update') or vr_cfg.get('start_date', '')
        if not last_str:
            return False
        try:
            last_dt = datetime.date.fromisoformat(last_str)
            months = int(vr_cfg.get('g_update_months', 6))
            return datetime.date.today() >= self._add_months(last_dt, months)
        except Exception:
            return False

    def months_since_g_update(self, vr_cfg):
        """마지막 G계수 증가 이후 경과 개월 수 (소수점 1자리, 30.44일/월 근사)"""
        last_str = vr_cfg.get('last_g_update') or vr_cfg.get('start_date', '')
        if not last_str:
            return None
        try:
            last_dt = datetime.date.fromisoformat(last_str)
            return round((datetime.date.today() - last_dt).days / 30.44, 1)
        except Exception:
            return None
