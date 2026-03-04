# AXI Traffic Generator — 작업일지

---

## 2026-03-05 (수)

### 1. Accumulate_and_Flush Pixel Progress 수정

**배경:** CAM_AE_Stat(64×64 블록 → 256B flush) BW plot이 막대 형태가 아닌 연속 선으로 표시됨.

**근본 원인 3가지:**
1. pipeline group 전체 pixel 합산 → flush 과다 발생 (2,864 vs 예상 2,025)
2. SBWC 압축 시 `pixels = bytes / bpp` 과소 계산 → PPC 기반 매 tick 카운트로 수정
3. tx pool 소진 시 pixel counting 중단 → `tx_finished` / `finished` 분리

**변경 내용:**
- `config_parser.py` — `BehaviorProfile.progress_source` 필드 추가
- `behavior.py` — `AccumulateAndFlushStrategy`가 특정 producer task의 pixel만 추적, `tx_finished` 사용
- `scheduler.py` — `Scoreboard`에 per-task pixel tracking, 매 tick PPC 기반 pixel counting, `total_frame_pixels` 기반 종료 판정, tx pool 크기 제한
- `Scenario_4K.yaml` / `Scenario_4K_SMMU.yaml` — `Progress_Source: "ISP_Write_Y"`

**검증 결과:**
| 항목 | 수정 전 | 수정 후 | 기대값 |
|------|---------|---------|--------|
| Burst 횟수 | 2,864 | **2,025** | 2,025 ✓ |
| 총 bytes | 733,184 | **518,400** | 518,400 ✓ |
| Burst 간격 | ~325 ticks | **1,024 ticks** | 1,024 ✓ |

---

### 2. Line-Based Dependency Gating 수정

**배경:** DISP_Read가 `Wait_For: ISP_Write_Y, Margin: 10`임에도 tick 10에서 바로 시작. dependency 게이트 미작동.

**원인:** `_check_dependency`에서 `done >= (0 - 10) = -10` 조건이 항상 true.

**변경 내용:**
- `scheduler.py` — `DmaAgent`에 `_bytes_emitted`, `stride`, `_line_progress` 추가
- `_check_dependency` — 실제 image line 기반: `producer._line_progress >= consumer._line_progress + margin`

**결과:** DISP_RD_0 시작: tick 10 → **tick 6,409** (ISP_Write_Y가 ~20줄 쓴 후)

---

### 3. Frame Granularity Dependency 추가

**배경:** Line 단위 dependency만 지원, Frame 단위(M2M 처리) 미지원.

**변경 내용:**
- `scheduler.py` — `_check_dependency`에 `Granularity: "Frame"` 분기 추가 (`producer.tx_finished` 기반)
- `DMA_IP_Spec.yaml` — `M2M_RD_0` IP 스펙 추가
- `Scenario_4K.yaml` — `M2M_Read` task 추가 (Frame dependency 예제)

**결과:** M2M_Read 시작: tick **1,252,813** (ISP_Write_Y 마지막 tick 1,252,794 직후)

---

### 4. Traffic Verification 섹션 추가

**배경:** trace_summary에서 실제 데이터량이 이론값과 일치하는지 확인하기 어려움.

**변경 내용:**
- `gen_summary.py` — **Traffic Verification** 섹션 추가
  - 각 DMA별 기대 byte 산출식 (SBWC header/payload 분해, block count 등)
  - 실제 vs 기대 비교 + PASS / FAIL / INCOMPLETE 판정
  - Burst 패턴 분석 (avg/max burst, avg_gap)
- `main.py` — `scenario` 객체를 `generate_summary`에 전달

**수정 파일:** `gen_summary.py`, `main.py`

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
