# Diagram 2 — Proposal lifecycle (Soul → Yudh → Apply, or rumination → kernel observation)

How a Soul thought becomes either an applied change, a kernel observation,
or an anomaly. The same diagram captures all three flows because they share
the upstream rumination step.

```mermaid
flowchart TB
    Start([Soul reads Meditation;<br/>detects pattern])

    Q1{Pattern targets a<br/>tunable category?}

    %% --- Tunable path ---
    PROP[Soul drafts proposal<br/>+ Meditation query<br/>+ supporting data<br/>+ pre-registered metric]

    YST{Yudh static check:<br/>well-formed?<br/>safety boundaries respected?}

    ANOM[/Anomaly record in<br/>Improvement Log<br/>'Soul attempted kernel-targeting'<br/>flagged for human attention/]

    YR[Yudh convenes reviewers<br/>scaled by blast radius<br/>reviewers query Meditation<br/>adversarial-by-default]

    REB{Soul rebuts<br/>with new evidence?}

    VER{Reviewers reach<br/>verdict?}

    HUM_TIE[Human as tiebreaker]

    VERD{Verdict:<br/>accept / reject /<br/>accept-with-mods}

    BLESS[Human blessing requested]

    BLESS_DEC{Blessed?}

    APPLY[Yudh applies diff to live state<br/>post-application metric tracking begins]

    PROP_LOG[/Proposal record in<br/>Improvement Log<br/>diff + evidence + transcript +<br/>verdict + blessing + post-metrics/]

    REJ_LOG[/Rejection in<br/>Improvement Log<br/>diff + evidence + transcript +<br/>reasons for rejection/]

    %% --- Kernel path ---
    KOBS[Soul drafts kernel observation<br/>+ pattern detected<br/>+ Meditation evidence<br/>+ what-Soul-would-propose-if-tunable]

    KLOG[/Kernel observation in<br/>Improvement Log<br/>awaiting human review/]

    HUM_REV[Human reviews on Sunday afternoon<br/>'acknowledged' / 'acted-upon' / 'dismissed']

    HUM_ACT{Human acts?}

    HUM_EDIT[Human edits kernel directly<br/>OR amends Catalogue<br/>'move category to tunable']

    NOTHING([Pattern stays as recorded observation;<br/>may accumulate over time])

    %% --- Wiring ---
    Start --> Q1
    Q1 -->|yes| PROP
    Q1 -->|no| KOBS

    PROP --> YST
    YST -->|fails| ANOM
    YST -->|passes| YR

    YR --> VER
    VER -->|disagree| HUM_TIE
    HUM_TIE --> VERD
    VER -->|agree| VERD

    VERD -->|reject| REJ_LOG
    VERD -->|accept-with-mods| REB
    VERD -->|accept| BLESS

    REB -->|yes| YR
    REB -->|no| BLESS

    BLESS --> BLESS_DEC
    BLESS_DEC -->|yes| APPLY
    BLESS_DEC -->|no| REJ_LOG

    APPLY --> PROP_LOG

    KOBS --> KLOG
    KLOG --> HUM_REV
    HUM_REV --> HUM_ACT
    HUM_ACT -->|yes| HUM_EDIT
    HUM_ACT -->|no| NOTHING

    %% Styles
    classDef start fill:#dbeafe,stroke:#1e40af,color:#1e3a8a,stroke-width:2px;
    classDef yudh fill:#fee2e2,stroke:#991b1b,color:#7f1d1d;
    classDef soul fill:#dcfce7,stroke:#15803d,color:#14532d;
    classDef human fill:#fef3c7,stroke:#b45309,color:#7c2d12;
    classDef log fill:#f3e8ff,stroke:#6b21a8,color:#581c87;
    classDef terminal fill:#f3f4f6,stroke:#374151,color:#111827;
    classDef anomaly fill:#fecaca,stroke:#7f1d1d,color:#7f1d1d,stroke-width:2px;

    class Start start;
    class PROP,KOBS soul;
    class YST,YR,VER,VERD,BLESS,APPLY yudh;
    class HUM_TIE,HUM_REV,HUM_EDIT,BLESS_DEC,HUM_ACT human;
    class PROP_LOG,REJ_LOG,KLOG log;
    class ANOM anomaly;
    class NOTHING terminal;
    class Q1,REB start;
```

## Reading the diagram

Three terminal states for a Soul thought:

1. **Applied change** (green-then-red-then-purple path). A tunable proposal makes it through static check, adversarial review, verdict, and human blessing. Lands in the live system. Recorded in the Improvement Log with full lifecycle.

2. **Kernel observation** (green-purple path on the right). Soul detected a pattern in a kernel category. Soul does not propose; it logs an observation. The human reviews. May or may not act. Either way, the observation persists.

3. **Anomaly** (red path on the left). Soul emitted a proposal targeting a kernel category — which a correctly-functioning Soul should not do. Static check rejects, but the rejection is itself a flag worth human attention.

## Two important properties visible here

**The kernel path has no Yudh.** Kernel observations bypass Yudh entirely. They go straight to the Improvement Log and wait for the human. This reflects that Yudh is the proceeding for proposals; kernel observations are not proposals.

**The human appears in three distinct roles.** Tiebreaker (when reviewers disagree), blesser (final approval before apply), and reviewer-of-the-log (Sunday-afternoon audit, acting on observations, amending the Catalogue). Same person, three contexts. The blessing role is what makes "hands-off proposing" coexist with "human still in the loop."
