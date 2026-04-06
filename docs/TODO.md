# TODO

## 완료
- [x] 매수 시 패턴 기반 포지션 사이징 금액과 리스크 max_per_trade_pct 충돌 — max_per_trade_pct 체크 제거, 포지션 사이징에 위임
- [x] 수익률 분석 리포트 — 오전 9시/오후 9시 자동 전송 + 텔레그램 `/report` 명령어
- [x] 패턴 적중률 추적 — 시그널 발생 4시간 후 가격 비교, pattern_accuracy DB 누적

## 향후 개선
- [ ] 패턴 신뢰도를 실제 적중률 기반으로 동적 교체 (pattern_accuracy → PatternMatch.confidence)
- [ ] 패턴 완성 조건 강화 (4, 5번 — 넥라인 깊이 비례, 동적 신뢰도)
