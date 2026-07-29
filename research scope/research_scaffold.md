# Research Paper Scaffold — ReqBridge

## Candidate Paper Titles

1. **"ReqBridge: An MCP-Native Multi-Agent Architecture for Automated Requirements Engineering and Azure DevOps Integration"**
2. **"Model Context Protocol as a First-Class Architecture Pattern for Enterprise AI Tooling: A Requirements Engineering Case Study"**
3. **"Confidence-Guided Human-in-the-Loop Orchestration for AI-Driven Requirements Decomposition"**
4. **"From Documents to DevOps: A LangGraph Multi-Agent Pipeline for End-to-End Requirements Traceability"**
5. **"Active Learning in Requirements Engineering: Leveraging Human Corrections to Improve AI Agent Accuracy Over Time"**

---

## Abstract Template (200 words)

Requirements engineering remains a bottleneck in enterprise software delivery, with manual conversion of stakeholder documents into structured Agile work items consuming [METRIC_1] hours per project on average. We present ReqBridge, a novel AI-powered system that automates the end-to-end pipeline from multi-modal document ingestion to Azure DevOps work item creation. ReqBridge introduces three key contributions: (1) a Model Context Protocol (MCP)-native architecture that exposes requirements engineering capabilities as composable AI tools, enabling integration with any MCP-compatible client; (2) a LangGraph multi-agent pipeline with [NUM_AGENTS] specialized agents orchestrated via confidence-based routing and human-in-the-loop approval gates; and (3) a live traceability knowledge graph maintaining bidirectional links from source documents through requirements to DevOps work items. In a [DURATION]-week deployment at [ORGANIZATION], ReqBridge achieved [METRIC_2]% reduction in requirements processing time, [METRIC_3]% precision in conflict detection, and [METRIC_4]% accuracy in work item generation compared to manual baselines. The system's active learning feedback loop improved agent accuracy by [METRIC_5]% over [NUM_ITERATIONS] iterations. Our results demonstrate that MCP-native agentic architectures represent a viable paradigm for enterprise AI tooling that balances automation with human oversight.

---

## Suggested Venues

### Tier 1 (Software Engineering)
- **ICSE** — International Conference on Software Engineering
- **ASE** — International Conference on Automated Software Engineering
- **RE** — IEEE International Requirements Engineering Conference
- **ICSSP** — International Conference on Software and Systems Process

### Tier 1 (Journals)
- **IEEE TSE** — Transactions on Software Engineering
- **JSS** — Journal of Systems and Software
- **IST** — Information and Software Technology
- **EMSE** — Empirical Software Engineering

### AI/Agent-Specific (Workshops & Conferences)
- **NeurIPS Workshop on Foundation Models for Decision Making**
- **ICLR Workshop on LLM Agents**
- **AAAI Workshop on AI for Software Engineering**
- **CHASE** — International Workshop on Cooperative and Human Aspects of SE (co-located with ICSE)
- **MCP Community Workshops** (emerging — Anthropic ecosystem)

---

## Experiment Design

### Metrics to Collect During Real-World Usage

| Metric | Measurement Method | Baseline Comparison |
|--------|-------------------|---------------------|
| Time savings vs. manual | Wall-clock time from document upload to ADO push vs. manual BA process | Historical project data |
| Work item quality scores | Blind evaluation by senior BAs (1-5 scale) on completeness, clarity, testability | Manually-created items |
| Conflict detection precision | TP/(TP+FP) where ground truth = expert-reviewed conflicts | Expert manual review |
| Conflict detection recall | TP/(TP+FN) for all genuine conflicts in test corpus | Expert manual review |
| ADO push success rate | Successful creates / total attempted creates | N/A (binary) |
| MCP call latency | End-to-end time for each MCP tool invocation | Direct API baseline |
| Confidence score calibration | Correlation between AI confidence and human agreement | Perfect calibration line |
| Per-agent accuracy over time | Corrections per agent per session over longitudinal deployment | Initial baseline |
| Traceability completeness | % of work items with full source→req→WI link chain | Manual traceability |
| User satisfaction (SUS) | System Usability Scale survey | Industry benchmark (68) |

### Experimental Protocol

1. **Baseline Phase (2 weeks)**: Measure manual requirements processing on 5 real projects
2. **Deployment Phase (6 weeks)**: Use ReqBridge on 10+ real projects with full instrumentation
3. **Comparison Phase**: Paired comparison of quality, time, and completeness
4. **Longitudinal**: Track feedback loop improvement over entire deployment

---

## Related Work Outline

### Requirements Engineering NLP
- Automated requirements extraction from natural language (Dalpiaz et al., 2019)
- Requirements classification using deep learning (Kurtanović & Maalej, 2017)
- Conflict detection in requirements specifications (Gervasi & Zowghi, 2005)
- Traceability link recovery using NLP (Cleland-Huang et al., 2014)

### Multi-Agent LLM Systems
- LangGraph and stateful agent orchestration (LangChain, 2024)
- Multi-agent collaboration patterns (Wu et al., 2023 — AutoGen)
- Human-in-the-loop AI systems (Amershi et al., 2019)
- Confidence calibration in LLM outputs (Kadavath et al., 2022)

### MCP as an Emerging Standard
- Model Context Protocol specification (Anthropic, 2024)
- Tool-use patterns in LLM architectures (Schick et al., 2023)
- Composable AI services and interoperability (emerging literature)
- Enterprise integration patterns for AI systems

### Agile & DevOps Automation
- Automated user story generation (Lucassen et al., 2016)
- AI-assisted backlog management (recent industry reports)
- DevOps tool integration and automation patterns

---

## Limitations and Future Work

### Limitations
- **Single-LLM dependency**: Current implementation relies solely on Claude; multi-model ensemble could improve robustness
- **English-only**: NLP pipeline not validated for multilingual requirements documents
- **Evaluation scale**: Initial deployment limited to a single organization; external validity requires multi-org studies
- **Prompt sensitivity**: Agent performance depends on prompt engineering quality; prompt optimization is manual
- **Cost considerations**: Per-token API costs may limit applicability for high-volume document processing

### Future Work
- **Multi-model ensemble**: Leverage multiple LLMs with consensus voting for higher-confidence extraction
- **Multilingual support**: Extend ingestion pipeline with translation and cross-lingual entity resolution
- **Visual requirements**: Enhanced diagram/wireframe understanding via Vision models for UI requirements
- **Automated prompt optimization**: Use DSPy or similar frameworks for programmatic prompt tuning
- **Cross-project learning**: Transfer learning from completed projects to new domains
- **Real-time collaboration**: Multi-user concurrent requirement review with conflict resolution
- **Regulatory compliance mapping**: Automated mapping of requirements to regulatory frameworks (SOX, GDPR)
- **Predictive analytics**: Use historical data to predict project risks from requirement patterns

---

## "Original Contribution to the Field" Section

*(For O-1/EB-1A petition evidence)*

### Statement of Originality

ReqBridge represents an original contribution to the fields of software engineering, artificial intelligence, and enterprise tooling through three novel architectural and algorithmic innovations:

**1. First MCP-Native Requirements Engineering Platform**

To our knowledge, ReqBridge is the first documented system that implements the Model Context Protocol (MCP) as a first-class architectural pattern for enterprise requirements engineering. Unlike systems that expose AI capabilities through traditional REST APIs, ReqBridge's MCP-native design enables any MCP-compatible AI client to perform requirements engineering operations directly, creating a new category of composable enterprise AI tools.

**2. Confidence-Gated Multi-Agent Pipeline with Live Traceability**

The system introduces a novel orchestration pattern where specialized AI agents are connected via a confidence-gated StateGraph with automatic routing to human-in-the-loop review nodes. Combined with a live bidirectional traceability knowledge graph (source → requirement → work item → DevOps), this architecture enables impact analysis capabilities impossible with traditional flat data models.

**3. Systematic Feedback Loop for Agent Improvement**

ReqBridge implements a formal active learning mechanism where human corrections are captured at the individual requirement level, attributed to specific agents, and used to systematically evolve prompt templates. Per-agent accuracy metrics computed over time provide the first longitudinal data on LLM-based requirements engineering quality improvement through human feedback.

### Impact Assessment

This work has potential impact on:
- **Industry practice**: Demonstrates a new paradigm for AI-assisted project coordination
- **Academic research**: Contributes empirical data on multi-agent AI systems in production settings
- **Standards evolution**: Provides evidence for MCP as an enterprise integration standard
- **Tool ecosystem**: Creates a reusable architecture pattern for domain-specific MCP servers

### Evidence of Novelty

A systematic literature search across IEEE Xplore, ACM Digital Library, and arXiv (as of 2025) reveals no prior work combining:
1. MCP as a primary system interface for requirements engineering
2. Multi-agent LangGraph orchestration with per-agent confidence scoring
3. Live traceability graphs spanning from source documents to DevOps work items
4. Active learning feedback loops in production requirements engineering tools
