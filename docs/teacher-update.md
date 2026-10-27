# Email draft

Subject: Travel Agent Progress Update and a Possible Reliability Study

Dear Professor Wang,

Following the Microsoft agent course you shared, I have built a travel-planning prototype using Microsoft Agent Framework. It supports Chinese and English requests, clarification for missing information, typed tool adapters, and validated JSON/Markdown output. I have also added local hybrid RAG, preference memory, checkpoints, and an optional reviewer.

My main focus has been reliability across the model-to-tool boundary. The model interprets the request, while the workflow controls execution and preserves tool evidence. For example, I found that a geocoder could confuse Victoria Harbour with a similarly named restaurant. I added place-type and identity checks, and separated planning preferences, such as an indoor/outdoor balance, from concrete locations. Tool failures are recorded explicitly, with successful results preserved.

The project currently passes 169 offline tests. I have also run real model/API cases and saved their traces, including tool status, latency, token usage, and failure attribution. Weather and place lookup use public APIs; flight/hotel inventory remains explicitly marked as demo data, and public-transit routing is not yet connected.

My next step is a held-out evaluation of when the agent fails, which layer causes the failure, and how alternative designs trade off correctness, coverage, latency, and token cost. Would this be a useful direction to develop toward a research project in your group? I would be happy to give a short demo and discuss the implementation.

Best,
Chelsea
