# TODO

## 완��
- [x] 매수 시 패턴 기반 포지��� 사이징 금액과 리스크 max_per_trade_pct 충돌 — max_per_trade_pct 체크 제거, 포지션 사이징에 위임
- [x] 수익률 분석 리포트 — 오전 9시/오후 9시 자동 전송 + 텔레그램 `/report` 명령어
- [x] 패턴 적중률 추적 �� 시그널 발생 4시간 후 가격 비교, pattern_accuracy DB 누적
- [x] 시그널 판단 4단계 구조 — S/R 메인, 패턴 보조, 지표 확인
- [x] 3개 스케줄러 통합 — unified_scheduler.py (캔들/일봉/리포트 단일 루프)

## 향후 개선
- [ ] 패턴 신뢰도를 실제 적중률 기반으로 동적 교체 (pattern_accuracy → PatternMatch.confidence)
- [ ] 패턴 완성 조건 강화 (넥라인 깊이 비례, 동적 신뢰도)
- [ ] 기울어진 넥라인, 손절가, 돌파 강도 구현 (패턴 개선 2차)
