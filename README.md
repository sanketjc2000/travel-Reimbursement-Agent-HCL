# Travel Reimbursement Approval Agent

A GenAI-powered agent that evaluates employee travel reimbursement claims against company policy using LangChain + Gemini.

## Quick Start

1. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

2. **Configure API key:**
   ```bash
   cp .env.example .env
   # Edit .env and add your GOOGLE_API_KEY from https://aistudio.google.com/apikey
   ```

3. **Run the notebook:**
   ```bash
   jupyter notebook sanketChavan.ipynb
   ```
   Run all cells top-to-bottom. The notebook is self-contained.

## Architecture

- **LangChain** single-agent with tool calling via Gemini LLM
- **Deterministic Python functions** for policy enforcement and financial calculations
- **Pydantic** schema validation for structured output
- **No** LangGraph, vector DB, RAG, or multi-agent setup needed

## Files

| File | Purpose |
|------|---------|
| `sanketChavan.ipynb` | Main notebook (single deliverable) |
| `requirements.txt` | Python dependencies |
| `.env` | API credentials (gitignored) |
| `.gitignore` | Git exclusions |
