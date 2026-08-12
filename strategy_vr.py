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

    # NOTE: 구 get_decision(일괄 리밸런싱 판정)은 사다리 방식 통일로 제거됨 — get_ladder_orders 사용
    def get_ladder_orders(self, ticker, current_price, current_qty, vr_cfg, range_pct=15.0, max_orders_per_side=20):
        """
        라오어 VR 원전 방식: 밴드 경계 터치 가격에 1주 단위 사다리 주문 생성
        - k번째 매수가 = V×(1-밴드) / (보유수량+k-1)  → 1주 살 때마다 다음 매수가 하락
        - k번째 매도가 = V×(1+밴드) / (보유수량-k+1)  → 1주 팔 때마다 다음 매도가 상승
        - 현재가 ±range_pct% 범위 내의 주문만 반환 (당일 유효 주문이므로 하루 변동폭만 커버)

        Returns:
            dict with keys: orders(list of {side, qty, price}), v, low, high,
                            range_low, range_high, portfolio, reason
        """
        base = {'orders': [], 'v': 0.0, 'low': 0.0, 'high': 0.0,
                'range_low': 0.0, 'range_high': 0.0, 'portfolio': 0.0}

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

        orders = []
        # 매수 사다리 (k가 커질수록 가격 하락)
        for k in range(1, max_orders_per_side + 1):
            price = round(low / (current_qty + k - 1), 2)
            if price < range_low:
                break
            if price > range_high:
                continue  # 이미 하한 이탈 상태의 캐치업 물량 중 범위 초과분 제외
            orders.append({'side': 'BUY', 'qty': 1, 'price': price})

        # 매도 사다리 (k가 커질수록 가격 상승, 보유수량 초과 매도 불가)
        for k in range(1, min(max_orders_per_side, current_qty) + 1):
            denom = current_qty - k + 1
            if denom <= 0:
                break
            price = round(high / denom, 2)
            if price > range_high:
                break
            if price < range_low:
                continue  # 이미 상한 이탈 상태의 캐치업 물량 중 범위 초과분 제외
            orders.append({'side': 'SELL', 'qty': 1, 'price': price})

        return {
            **base,
            'orders': orders,
            'v': v,
            'low': round(low, 2),
            'high': round(high, 2),
            'range_low': round(range_low, 2),
            'range_high': round(range_high, 2),
            'portfolio': round(current_qty * current_price, 2),
            'reason': f'사다리 {len(orders)}건 (현재가 ±{range_pct:.0f}% 범위)',
        }

    def format_ladder_table(self, orders, current_price):
        """사다리 주문표 HTML 렌더링 (스케줄러/수동 버튼 공용)"""
        buy_lines = [o for o in orders if o['side'] == 'BUY']
        sell_lines = [o for o in orders if o['side'] == 'SELL']

        table = "\n\n━━━━━━━━━━━━━━━━━━━\n📋 <b>주문표</b> (1주 × 지정가, 당일 유효)\n"
        # 매도 사다리는 높은 가격부터, 현재가 구분선 아래로 매수 사다리
        for o in sorted(sell_lines, key=lambda x: -x['price']):
            table += f"🔴 매도 1주 × <b>${o['price']:.2f}</b>\n"
        table += f"— 현재가 ${current_price:.2f} —\n"
        for o in sorted(buy_lines, key=lambda x: -x['price']):
            table += f"🟡 매수 1주 × <b>${o['price']:.2f}</b>\n"
        table += f"\n총 매수 {len(buy_lines)}건 / 매도 {len(sell_lines)}건 — 주문을 실행하시겠습니까?"
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
