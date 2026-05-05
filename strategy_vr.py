# ==========================================================
# [strategy_vr.py] - VR5 밸류리밸런싱 전략 엔진
# NEW: 라오어 밸류리밸런싱(Value Rebalancing) 투자법 구현
# - V: 목표 포트폴리오 가치
# - 밴드: V×(1±band_pct%) 범위 이탈 시 매수/매도
# - V 업데이트 주기: 2주마다 (V2 = V1 + Pool/G ± deposit)
# - G: 분할 계수 (적립식=10)
# - 주문 방식: 지정가(LIMIT)
# ==========================================================
import math
import datetime
import logging


class VRStrategy:
    """VR5 밸류리밸런싱 전략 엔진 (V14/V-REV와 완전 독립)"""

    def get_decision(self, ticker, current_price, current_qty, vr_cfg):
        """
        VR5 밴드 체크 후 매수/매도/홀드 결정 반환

        Returns:
            dict with keys: action, qty, price, portfolio, v,
                            low_target, high_target, reason
        """
        if not vr_cfg.get('enabled'):
            return {'action': 'HOLD', 'reason': 'VR 비활성화'}

        v = float(vr_cfg.get('v_value', 0))
        if v <= 0:
            return {'action': 'HOLD', 'reason': 'V 값 미설정'}

        if current_price <= 0:
            return {'action': 'HOLD', 'reason': '현재가 조회 실패'}

        band_pct = float(vr_cfg.get('band_pct', 15)) / 100.0
        portfolio = current_qty * current_price
        low_target = v * (1 - band_pct)
        high_target = v * (1 + band_pct)

        base = {
            'portfolio': round(portfolio, 2),
            'v': v,
            'low_target': round(low_target, 2),
            'high_target': round(high_target, 2),
        }

        if portfolio < low_target:
            # 포트폴리오를 V까지 끌어올리는 매수량
            buy_value = v - portfolio
            buy_qty = math.floor(buy_value / current_price)
            if buy_qty <= 0:
                return {**base, 'action': 'HOLD', 'reason': '매수 수량 0 (갭 미달)'}
            return {
                **base,
                'action': 'BUY',
                'qty': buy_qty,
                'price': current_price,
                'reason': f'포폴 ${portfolio:,.0f} < 하한 ${low_target:,.0f}',
            }

        if portfolio > high_target:
            # 포트폴리오를 V까지 낮추는 매도량
            sell_value = portfolio - v
            sell_qty = math.floor(sell_value / current_price)
            sell_qty = min(sell_qty, current_qty)
            if sell_qty <= 0:
                return {**base, 'action': 'HOLD', 'reason': '매도 수량 0'}
            return {
                **base,
                'action': 'SELL',
                'qty': sell_qty,
                'price': current_price,
                'reason': f'포폴 ${portfolio:,.0f} > 상한 ${high_target:,.0f}',
            }

        return {
            **base,
            'action': 'HOLD',
            'reason': f'밴드 내 정상 (${low_target:,.0f}~${high_target:,.0f})',
        }

    def calc_next_v(self, vr_cfg, deposit=0.0):
        """
        다음 V값 계산: V2 = V1 + Pool/G ± deposit
        - Pool: 별도 관리 투자 풀
        - G   : 분할 계수 (적립식=10)
        - deposit: 이번 주기 추가 입금(+) / 출금(-)
        """
        v1 = float(vr_cfg.get('v_value', 0))
        pool = float(vr_cfg.get('pool', 0))
        g = int(vr_cfg.get('g_factor', 10))

        v_increment = (pool / g) if g > 0 else 0.0
        v2 = v1 + v_increment + deposit
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
