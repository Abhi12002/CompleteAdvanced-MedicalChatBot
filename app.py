from flask import Flask, render_template, request
from dotenv import load_dotenv
import os

from src.helper import download_hugging_face_embeddings
from langchain_pinecone import PineconeVectorStore
from langchain_openai import ChatOpenAI
from langchain.chains import create_retrieval_chain
from langchain.chains.combine_documents import create_stuff_documents_chain
from langchain_core.prompts import ChatPromptTemplate, MessagesPlaceholder

# NEW: memory imports
from langchain_core.runnables.history import RunnableWithMessageHistory
from langchain_community.chat_message_histories import ChatMessageHistory

from src.prompt import system_prompt

app = Flask(__name__)
load_dotenv()

# --- keys ---
PINECONE_API_KEY = os.environ.get("PINECONE_API_KEY")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY")
os.environ["PINECONE_API_KEY"] = PINECONE_API_KEY or ""
os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY or ""

# --- embeddings + retriever ---
embeddings = download_hugging_face_embeddings()
index_name = "medical-chatbot"

docsearch = PineconeVectorStore.from_existing_index(
    index_name=index_name,
    embedding=embeddings
)
retriever = docsearch.as_retriever(search_type="similarity", search_kwargs={"k": 3})

# --- LLM + prompt (memory-aware) ---
chatModel = ChatOpenAI(model="gpt-4o", temperature=0)
prompt = ChatPromptTemplate.from_messages([
    ("system", system_prompt),
    MessagesPlaceholder("history"),   # <- memory goes here
    ("human", "{input}"),
])

question_answer_chain = create_stuff_documents_chain(chatModel, prompt)
rag_chain = create_retrieval_chain(retriever, question_answer_chain)

# --- Ephemeral in-RAM memory (keyed by SID from client; dies on refresh) ---
_store = {}  # {sid: ChatMessageHistory()}

def _get_history(session_id: str) -> ChatMessageHistory:
    return _store.setdefault(session_id, ChatMessageHistory())

chat_with_memory = RunnableWithMessageHistory(
    rag_chain,
    _get_history,
    input_messages_key="input",
    history_messages_key="history",
    output_messages_key="answer",
)

@app.route("/")
def index():
    return render_template("chat.html")

@app.post("/get")
def chat():
    sid = request.form.get("sid")          # required per-tab session id
    if not sid:
        return "sid required", 400

    msg = request.form.get("msg", "").strip()
    if not msg:
        return "", 200

    result = chat_with_memory.invoke(
        {"input": msg},
        config={"configurable": {"session_id": sid}}
    )
    answer = result["answer"]

    # Optional: cap memory size so it never grows unbounded
    hist = _get_history(sid)
    MAX_MSGS = 24  # keep ~12 exchanges (user+bot)
    if len(hist.messages) > MAX_MSGS:
        hist.messages[:] = hist.messages[-MAX_MSGS:]

    return str(answer)

@app.post("/clear")
def clear():
    sid = request.form.get("sid")
    if sid:
        _store.pop(sid, None)
    return ("", 204)

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8080, debug=True)
