## Approach for baselining Agent

Given the 'XYZ Dataset', please write some python code to ``<insert query>`` and output the detection results.
This was the general process:

* *User Problem*: Given the “MIT-BIH Arrhythmia Database”, please load all the ECG records from my local disk and detect all the R-peaks. Then, for each record, output its name along with the detection accuracy, adhering to the specified output format below.
* *Remarks*: Only download zip file from the website.
* *Input*: Path to the dataset.
* *Output format*: Case {}...

One thing to note - Agent may be working with models locally. There is no remote processing at LLM servers.
This avoids round-trip transmission of prompts and results over the network, which increases response latency.

_Trick_: "We note that although the program synthesis process needs communication and interaction with remote LLM servers, the synthesized program can be executed locally on the client side."

### Three key parts to Agent

Agent is an LLM-driven automated natural programming system that synthesizes programs for AIoT applications. It features three key modules:

* *Background knowledge retrieval*: Collects domain knowledge from the internet for in-context learning.
* *Automated program synthesis*: Decomposes an AIoT task into several subtasks and generates corresponding functional code snippets.
* *Code improvement module*: Executes the synthesized program and feeds the compiler and interpreter feedback to the LLM, facilitating iterative code correction and improvement.

The code improvement module enables automated debugging, removing the human from the loop.
"This code optimization cycle is not a one-time process but is repeated multiple times. Empirically, Agent takes five iterations to progressively generate five different programs, striking a balance between thoroughness and efficiency.
Finally, Agent requests the LLM to analyze the execution results of all the programs and select the one that achieves the best performance as the final program."

_Hypothesis_: While there may not be a big difference in accuracy between Flash-Fusion and Agent, there is a strong possibility that input token consumption and latency for the latter will be far higher.

### Few implementation details

Some details to consider to enable an apples-to-apples comparison:

* Agent is implemented with GPT-4 based on LangChain, which provides various tools (e.g., web search engine,
vector database, etc) for LLMs to collect relevant information.
* Tavily is selected as the web search tool to search for relevant information. It uses OpenAI’s text embedding model to convert the retrieved webpages into vector representations.
* Then uses Faiss for efficient similarity search of vector representations.
* Deployed on a Linux Ubuntu workstation equipped with an NVIDIA RTX 4090 GPU. This last bit may be hard to do but if I implement baselines and FF on macOS, it would still be an apples-to-apples comparison.

### Agent's baseline ideas

* For heartbeat detection, five representative baseline algorithms were used, including Hamilton, Christov, Engzee, Pan-Tompkins, and SWT.
* With regards to IMU-based HAR (the WISDM dataset), five open-source GitHub repositories were used: LSTM-RNN, 1D-CNN, Conv-LSTM, BiLSTM, and NN.

### Ideas for LLM-Only baseline:

Maybe we can find a name for this baseline that can be linked to a published paper.

* Raw sensor data is transmitted from users to LLM servers, raising privacy concerns about sensor data.
* Limited by token size, existing work downsamples and quantizes raw sensor data, leading to degraded sensor performance.

#### What Agent's related works section suggests

* Prompt-based methods embed raw sensor data into tailored prompts and instruct LLMs to perform various AIoT tasks. HARGPT and LLMSense embed textualized sensor data into prompts to show the proficiency of LLMs in comprehending IoT sensor data. Requires transmitting raw sensor data to LLM servers, suffering from similar issues.