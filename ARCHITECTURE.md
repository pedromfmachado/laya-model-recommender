# Architecture

Model Recommender runs a local classifier before Codex sends each prompt. Codex hosts the plugin's MCP server and invokes its recommendation tool through a synchronous `UserPromptSubmit` hook. Model recommendations are limited to the first prompt in each thread; reasoning recommendations are checked on every prompt. The hook can let a prompt continue or pause it while the user changes settings.

## Components

| Component | Responsibility |
| --- | --- |
| Codex host | Starts the plugin MCP server, calls the prompt hook, displays elicitation choices, and applies the hook result. |
| Plugin runtime | Checks whether setup is ready; if setup is missing or stale, prepares the private Python runtime, dependencies, and local classifier model before starting the MCP server. |
| Recommender MCP server | Receives the prompt, session ID, and selected model; runs the first-prompt model guard, reads active reasoning metadata through Codex app-server, and asks what to do when a recommendation differs. |
| Local Laya classifier | Classifies model tier and reasoning effort using the configured questions. After setup, inference runs locally and offline. |
| User | Chooses whether to use current settings or change them. The plugin does not change model or reasoning settings. |

## Communication flow

```mermaid
sequenceDiagram
    actor User
    participant Codex as Codex host and UI
    participant Runtime as Plugin runtime
    participant MCP as Recommender MCP server
    participant Laya as Local Laya classifier

    Codex->>Runtime: Start stdio MCP server
    Runtime->>Runtime: Check setup readiness and source fingerprint
    opt Setup missing or stale
        Runtime->>Laya: Install dependencies and preload local model
    end
    Runtime-->>Codex: MCP server ready

    User->>Codex: Submit prompt
    Codex->>MCP: Hook call with prompt, session ID, and selected model
    MCP->>MCP: Claim first-prompt model eligibility once per thread
    MCP->>Laya: Classify reasoning, and model tier on the first prompt
    Laya-->>MCP: Capability and reasoning demand
    MCP->>Codex: Read thread reasoning and model defaults through app-server
    Codex-->>MCP: Active reasoning setting, when resolvable
    MCP->>MCP: Compare first-prompt model and per-prompt reasoning recommendations

    alt No known setting differs
        MCP-->>Codex: Recommendation or confirmation; do not block
        Codex->>Codex: Continue submitted prompt
    else A known setting differs
        MCP->>Codex: Elicit recommendation and action choices
        Codex-->>User: Show the question and choices
        User->>Codex: Choose an action
        Codex-->>MCP: Return the choice
        alt Use current settings
            MCP-->>Codex: Allow prompt to continue
            Codex->>Codex: Continue submitted prompt
        else Change settings
            MCP-->>Codex: Block prompt and add original request as context
            Codex->>Codex: Record context and stop this submission
            User->>Codex: Change settings and send a short follow-up
            Codex->>MCP: Hook call for follow-up
            MCP->>MCP: Skip model recommendation; classify reasoning again
            Codex->>Codex: Resume with the preserved request context
        end
    end
```

## State and decision behavior

- A local hashed session-root marker is claimed on the first valid hook call, before app-server lookup. A model recommendation is shown on that call only if the unique root thread resolves; a lookup failure cannot defer it to a later prompt. Reasoning is classified and checked on every call.
- The active reasoning setting comes from Codex app-server thread metadata, falling back to the selected model's default when the thread does not report an explicit effort. If that value cannot be resolved, the reasoning comparison is skipped and the prompt continues.
- The hook session ID identifies a session tree, which can contain forked threads. Model eligibility is shared by that session tree. Recommendations run only when bounded, paginated app-server metadata shows a single thread for that session ID and that thread is the root. Forked trees, incomplete listings, and unresolved settings fail open because the hook does not identify the active fork before submission.
- When a prompt is blocked and the user sends a short generic continuation, the classifier reuses the blocked request saved for that resolved thread. A substantive new prompt replaces the saved request and takes precedence.
- Choosing current settings allows the submitted prompt to continue. Choosing to change settings blocks that submission and adds the original prompt as `UserPromptSubmit` additional context; after changing settings, the user can send a short follow-up to resume it.
- A recommendation or elicitation error is reported to the user and the prompt continues with current settings. If the MCP server cannot start or connect, the hook cannot show the recommendation question.
