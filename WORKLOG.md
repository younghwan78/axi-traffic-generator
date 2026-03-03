# AXI Traffic Generator — 작업일지

---

## 2026-03-04 (화)

### 1. IP-DMA 계층 구조 + Instance Replication

**배경:** 기존 구조는 IP 하나당 DMA 하나로 가정. 실제 하드웨어는 IP 하나에 DMA 10개 이상 가능.

**변경 내용:**
- `DMA_IP_Spec.yaml`에 `IP` 필드(그룹명)와 `Instances` 키(자동 복제) 추가
  ```yaml
  CAM_ISP_WR:
    IP: CAM_ISP
    Instances: [CAM_ISP_WR_0, CAM_ISP_WR_1]  # 템플릿에서 자동 복제
  ```
- `config_parser.py` — `DmaIpSpec.ip_group` 필드 + Instances 확장 로직
- `gen_summary.py` — Transaction Overview, BW Breakdown, Config Summary 모두 IP별 계층 출력 + Subtotal
- `main.py` — `ip_configs`에 `ip_group` 전달

**수정 파일:** `DMA_IP_Spec.yaml`, `config_parser.py`, `gen_summary.py`, `main.py`, `Scenario_4K.yaml`, `Scenario_4K_SMMU.yaml`

**검증:** 5 DMA (Instances 확장 포함) 정상 동작, 652,248 tx, 64B compliance ✓

---

### 2. Clock-Proportional Scheduling (Cycle-Accurate)

**배경:** 기존 스케줄러는 모든 agent가 매 tick 동일하게 step. 800MHz와 533MHz agent 구분 없음.

**변경 내용:**
- `scheduler.py`의 `VirtualTickScheduler.run()`에 clock accumulator 추가
- 높은 clock agent가 더 자주 step (800MHz는 533MHz 대비 1.5배)
- 변경량: 약 6줄

**결과 비교:**
| 항목 | 변경 전 | 변경 후 |
|------|---------|---------|
| DISP_RD_0 B/tick | 2.5 | 1.7 |
| DISP_RD_0 GB/s | 1.33 | 0.89 |
| Simulation Duration | 4,976,616 | 7,469,595 ticks |

**수정 파일:** `scheduler.py`

---

### 3. SBWC 압축 포맷 지원

**배경:** SBWC(Samsung Bandwidth Compression) 미지원 상태. Header+Payload 분리 접근 패턴, 포맷별 블록 정렬(YUV: 32×4, Bayer: 256×1) 필요.

**변경 내용:**
- `format_descriptor.py`
  - `SBWC_BLOCK_DB` — 포맷 계열별 블록 크기 정의
  - `FORMAT_DB`에 SBWC 엔트리 추가 (`SBWC_YUV420_8bit` 등)
  - `SbwcDescriptor` 클래스 — Header(32B align) / Payload(128B align) 크기 계산
- `domain_model.py` — `AxiTransaction.cache` 속성 (`Normal` / `SBWC_Alloc`)
- `generator.py` — `_generate_sbwc_header_stream()`, `_generate_sbwc_payload_stream()` 추가
- `config_parser.py` — `TaskConfig.sbwc_ratio` 필드
- `scheduler.py` — `sbwc_ratio` 파라미터 전달

**Scenario 예제:**
```yaml
Format: "SBWC_YUV420_8bit"
SBWC_Ratio: 0.5    # 50% 압축
```

**검증 결과:**
| 항목 | SBWC OFF | SBWC 0.5 |
|------|----------|----------|
| 전체 TX | 652,248 | 458,981 (▼30%) |
| 총 데이터량 | 39.81 MB | 24.00 MB (▼40%) |
| SBWC_Alloc TX | 0 | 447,525 |
| 64B Compliance | ✓ | ✓ |

**수정 파일:** `format_descriptor.py`, `domain_model.py`, `generator.py`, `config_parser.py`, `scheduler.py`, `Scenario_4K.yaml`, `main.py`

---

### 4. BW Graph HTML 리포트

**배경:** 시간축 BW 변화를 시각적으로 확인할 수 있는 차트 필요.

**변경 내용:**
- `gen_bw_chart.py` (신규) — Plotly.js 기반 인터랙티브 HTML 차트
  - **Chart 1:** IP-Level Bandwidth (R/W 분리, DMA 합산)
  - **Chart 2:** Per-DMA Bandwidth Breakdown
  - 1,000 tick 단위 time bin, hover/zoom 지원, 다크 테마
- `main.py` — 시뮬레이션 후 자동으로 `trace_bw.html` 생성

**출력:** `output/trace_bw.html` (598KB, 브라우저에서 열어서 확인)

**수정 파일:** `gen_bw_chart.py` (신규), `main.py`

---

### 이전 세션 요약 (2026-03-03)

- **DMA Configuration Summary** 추가 — `gen_summary.py`에 BurstLen, PPC, BPP 등 HW 설정 표시
- **Z-order 접근 패턴** 예제 적용 — `Scenario_4K.yaml`의 ISP Write에 Z-order
- **SMMU 시나리오** 신규 — `Scenario_4K_SMMU.yaml` (PTW injection 포함)
- **Clock Domain 리팩터링** — `Clock_Domains` 간접 참조 제거, 각 Task에 직접 MHz 지정
- **.venv 환경 구축** 및 기본 예제 실행 검증

---

### 현재 프로젝트 파일 구조

```
21_MMIP_TG/
├── main.py              # 메인 오케스트레이터
├── config_parser.py     # YAML 파서 (IP Spec + Scenario)
├── scheduler.py         # Virtual Tick Scheduler (clock-proportional)
├── generator.py         # Stream Generator (SBWC 지원)
├── format_descriptor.py # 포맷 DB + SBWC Descriptor
├── domain_model.py      # AxiTransaction 모델
├── gen_summary.py       # 텍스트 Summary 리포트
├── gen_bw_chart.py      # BW Chart HTML 리포트 (NEW)
├── DMA_IP_Spec.yaml     # DMA 하드웨어 스펙 (IP 그룹 + Instances)
├── Scenario_4K.yaml     # 4K 시나리오 (SBWC 적용)
├── Scenario_4K_SMMU.yaml # SMMU 활성화 시나리오
└── output/
    ├── trace.txt         # AXI 트랜잭션 트레이스
    ├── trace_summary.txt # 텍스트 Summary
    └── trace_bw.html     # BW 차트 (NEW)
```
