import sqlite3
import struct
import json
import time



class ConversationVector:

    def __init__(self, message_id, message_index, role, text, embedding, timestamp, thread_path, similarity=0.0):
        self.message_id = message_id
        self.message_index = message_index
        self.role = role
        self.text = text
        self.embedding = embedding
        self.timestamp = timestamp
        self.thread_path = thread_path
        self.similarity = similarity

def pack_float_vector(vector):
    if not vector:
        return b''
    format_string = '<' + str(len(vector)) + 'f'
    return struct.pack(format_string, *vector)

def unpack_float_vector(blob):
    if not blob:
        return []
    num_floats = len(blob) // 4
    format_string = '<' + str(num_floats) + 'f'
    unpacked = struct.unpack(format_string, blob)
    result = []
    for val in unpacked:
        result.append(val)
    return result

def cosine_similarity(v1, v2):
    if not v1 or not v2 or len(v1) != len(v2):
        return 0.0
    dot_product = 0.0
    norm_v1_sq = 0.0
    norm_v2_sq = 0.0
    for i in range(len(v1)):
        dot_product += v1[i] * v2[i]
        norm_v1_sq += v1[i] * v1[i]
        norm_v2_sq += v2[i] * v2[i]
    norm_v1 = norm_v1_sq ** 0.5
    norm_v2 = norm_v2_sq ** 0.5
    if norm_v1 == 0.0 or norm_v2 == 0.0:
        return 0.0
    return dot_product / (norm_v1 * norm_v2)

class VectorDatabase:

    def __init__(self, db_path):
        self.db_path = db_path
        self._initialize_database()

    def _initialize_database(self):
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()

        cursor.execute("""
            CREATE TABLE IF NOT EXISTS conversation_vectors (
                message_id TEXT PRIMARY KEY,
                message_index INTEGER NOT NULL,
                role TEXT NOT NULL,
                content TEXT NOT NULL,
                embedding BLOB NOT NULL,
                timestamp REAL NOT NULL,
                thread_path TEXT NOT NULL
            )
        """)
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS topic_summaries (
                node_id     TEXT PRIMARY KEY,
                topic_name  TEXT NOT NULL,
                summary     TEXT NOT NULL,
                embedding   BLOB NOT NULL,
                start_index INTEGER,
                end_index   INTEGER,
                depth       INTEGER
            )
        """)
        conn.commit()
        conn.close()




    def add_conversation_message(self, msg):
        packed_vector = pack_float_vector(msg.embedding)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('\n            INSERT OR REPLACE INTO conversation_vectors (\n                message_id, message_index, role, content, embedding, timestamp, thread_path\n            ) VALUES (?, ?, ?, ?, ?, ?, ?)\n        ', (msg.message_id, msg.message_index, msg.role, msg.text, packed_vector, msg.timestamp, msg.thread_path))
        conn.commit()
        conn.close()



    def search_conversation(self, query_vector, top_k=2, min_similarity=0.5):
        if not query_vector:
            return []
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('\n            SELECT message_id, message_index, role, content, embedding, timestamp, thread_path \n            FROM conversation_vectors\n        ')
        rows = cursor.fetchall()
        conn.close()
        scored_msgs = []
        for row in rows:
            msg_id = row[0]
            msg_idx = row[1]
            role = row[2]
            content = row[3]
            raw_embed = row[4]
            timestamp = row[5]
            thread_path = row[6]
            stored_vector = unpack_float_vector(raw_embed)
            sim = cosine_similarity(query_vector, stored_vector)
            if sim >= min_similarity:
                data_tuple = (msg_id, msg_idx, role, content, timestamp, thread_path)
                scored_msgs.append((sim, data_tuple))
        scored_msgs.sort(key=lambda x: x[0], reverse=True)
        top_matches = []
        for i in range(min(top_k, len(scored_msgs))):
            top_matches.append(scored_msgs[i])
        results = []
        for match in top_matches:
            sim = match[0]
            data = match[1]
            msg_id = data[0]
            msg_idx = data[1]
            role = data[2]
            content = data[3]
            timestamp = data[4]
            thread_path = data[5]
            msg = ConversationVector(message_id=msg_id, message_index=msg_idx, role=role, text=content, embedding=[], timestamp=timestamp, thread_path=thread_path, similarity=sim)
            results.append(msg)
        return results

    # ── Topic Summary Methods (Change 1: Surgical Retrieval) ─────────────────

    def upsert_topic_summary(self, node_id, topic_name, summary, embedding,
                             start_index=None, end_index=None, depth=None):
        """Insert or replace a topic embedding row. Called when a node is frozen & summarised."""
        packed = pack_float_vector(embedding)
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            'INSERT OR REPLACE INTO topic_summaries '
            '(node_id, topic_name, summary, embedding, start_index, end_index, depth) '
            'VALUES (?, ?, ?, ?, ?, ?, ?)',
            (node_id, topic_name, summary, packed, start_index, end_index, depth)
        )
        conn.commit()
        conn.close()

    def search_topic_summaries(self, query_vector, top_k=3, min_similarity=0.40):
        """Cosine-search the topic_summaries table.
        Returns list of dicts: [{node_id, topic_name, summary, similarity}]
        """
        if not query_vector:
            return []
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute(
            'SELECT node_id, topic_name, summary, embedding FROM topic_summaries'
        )
        rows = cursor.fetchall()
        conn.close()
        scored = []
        for row in rows:
            node_id, topic_name, summary, raw_embed = row
            stored_vec = unpack_float_vector(raw_embed)
            sim = cosine_similarity(query_vector, stored_vec)
            if sim >= min_similarity:
                scored.append((sim, node_id, topic_name, summary))
        scored.sort(key=lambda x: x[0], reverse=True)
        results = []
        for sim, node_id, topic_name, summary in scored[:top_k]:
            results.append({
                'node_id': node_id,
                'topic_name': topic_name,
                'summary': summary,
                'similarity': sim,
            })
        return results

    def delete_topic_summary(self, node_id):
        """Remove a topic summary row by node_id (used by reorganizer on merge)."""
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute('DELETE FROM topic_summaries WHERE node_id = ?', (node_id,))
        conn.commit()
        conn.close()