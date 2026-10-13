/**
 * Agent Integration Helper for Odysseus
 * 
 * Provides utilities for agents to interact with Notes and Todos
 * Improves readability, updating, and summarization capabilities
 */

class AgentIntegration {
    /**
     * Initialize agent integration
     */
    constructor() {
        this.notesEndpoint = '/api/codex/notes';
        this.todosEndpoint = '/api/codex/todos';
        this.memoryEndpoint = '/api/codex/memory';
    }

    /**
     * Read and parse notes for agent consumption
     * @param {string} noteId - Optional specific note ID
     * @returns {Promise<Array>} Array of note objects with structured data
     */
    async readNotes(noteId = null) {
        try {
            let url = this.notesEndpoint;
            if (noteId) {
                url = `${this.notesEndpoint}/${noteId}`;
            }
            
            const response = await fetch(url);
            const notes = await response.json();
            
            // Structure notes for agent consumption
            return this._structureNotesForAgent(notes);
        } catch (error) {
            console.error('AgentIntegration: Error reading notes', error);
            return [];
        }
    }

    /**
     * Structure notes for agent consumption
     * @param {Array|Object} notes - Raw note data
     * @returns {Array} Structured note data
     */
    _structureNotesForAgent(notes) {
        if (!Array.isArray(notes)) {
            notes = [notes];
        }
        
        return notes.map(note => ({
            id: note.id,
            title: note.title || 'Untitled Note',
            content: note.content || '',
            createdAt: note.created_at,
            updatedAt: note.updated_at,
            tags: note.tags || [],
            metadata: {
                wordCount: this._countWords(note.content),
                characterCount: note.content ? note.content.length : 0,
                hasCode: this._containsCode(note.content),
                language: this._detectLanguage(note.content)
            },
            structuredContent: this._parseNoteContent(note.content)
        }));
    }

    /**
     * Parse note content into structured format
     * @param {string} content - Note content
     * @returns {Object} Structured content
     */
    _parseNoteContent(content) {
        if (!content) return {};
        
        return {
            sections: this._extractSections(content),
            bulletPoints: this._extractBulletPoints(content),
            keyValuePairs: this._extractKeyValuePairs(content),
            codeBlocks: this._extractCodeBlocks(content),
            questions: this._extractQuestions(content)
        };
    }

    /**
     * Update or create a note
     * @param {string} noteId - Note ID (optional for new notes)
     * @param {string} title - Note title
     * @param {string} content - Note content
     * @param {Array} tags - Note tags
     * @returns {Promise<Object>} Updated note object
     */
    async updateNote(noteId, title, content, tags = []) {
        try {
            const method = noteId ? 'PUT' : 'POST';
            const url = noteId ? `${this.notesEndpoint}/${noteId}` : this.notesEndpoint;
            
            const response = await fetch(url, {
                method: method,
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    title: title,
                    content: content,
                    tags: tags
                })
            });
            
            return await response.json();
        } catch (error) {
            console.error('AgentIntegration: Error updating note', error);
            throw error;
        }
    }

    /**
     * Summarize notes for agent consumption
     * @param {string|Array} noteIds - Note ID or array of note IDs
     * @param {number} maxLength - Maximum summary length
     * @returns {Promise<string>} Summary of notes
     */
    async summarizeNotes(noteIds, maxLength = 200) {
        try {
            const notes = await this.readNotes(noteIds);
            return this._generateNoteSummary(notes, maxLength);
        } catch (error) {
            console.error('AgentIntegration: Error summarizing notes', error);
            return '';
        }
    }

    /**
     * Generate summary from note content
     * @param {Array} notes - Array of note objects
     * @param {number} maxLength - Maximum summary length
     * @returns {string} Generated summary
     */
    _generateNoteSummary(notes, maxLength) {
        if (!notes || notes.length === 0) return 'No notes available.';
        
        // Combine all note content
        const allContent = notes.map(note => note.content).join('\n\n');
        
        // Simple summarization - extract key sentences
        const sentences = allContent.split(/[.!?]\s+/);
        let summary = '';
        
        for (const sentence of sentences) {
            if ((summary + sentence).length <= maxLength) {
                summary += sentence + '. ';
            } else {
                break;
            }
        }
        
        // If summary is too short, use first part of content
        if (summary.length < 50 && allContent.length > 0) {
            summary = allContent.substring(0, maxLength) + (allContent.length > maxLength ? '...' : '');
        }
        
        return summary.trim();
    }

    /**
     * Read and parse todos for agent consumption
     * @param {string} todoId - Optional specific todo ID
     * @returns {Promise<Array>} Array of todo objects with structured data
     */
    async readTodos(todoId = null) {
        try {
            let url = this.todosEndpoint;
            if (todoId) {
                url = `${this.todosEndpoint}/${todoId}`;
            }
            
            const response = await fetch(url);
            const todos = await response.json();
            
            // Structure todos for agent consumption
            return this._structureTodosForAgent(todos);
        } catch (error) {
            console.error('AgentIntegration: Error reading todos', error);
            return [];
        }
    }

    /**
     * Structure todos for agent consumption
     * @param {Array|Object} todos - Raw todo data
     * @returns {Array} Structured todo data
     */
    _structureTodosForAgent(todos) {
        if (!Array.isArray(todos)) {
            todos = [todos];
        }
        
        return todos.map(todo => ({
            id: todo.id,
            title: todo.title || 'Untitled Task',
            description: todo.description || '',
            status: todo.status || 'pending',
            dueDate: todo.due_date,
            createdAt: todo.created_at,
            updatedAt: todo.updated_at,
            priority: todo.priority || 'medium',
            tags: todo.tags || [],
            dependencies: todo.dependencies || [],
            metadata: {
                isOverdue: todo.due_date ? new Date(todo.due_date) < new Date() : false,
                daysUntilDue: todo.due_date ? this._daysUntilDue(todo.due_date) : null
            }
        }));
    }

    /**
     * Create or update a todo
     * @param {string} todoId - Todo ID (optional for new todos)
     * @param {string} title - Todo title
     * @param {string} description - Todo description
     * @param {string} status - Todo status
     * @param {string} dueDate - Due date
     * @param {string} priority - Priority level
     * @param {Array} tags - Tags
     * @param {Array} dependencies - Dependency IDs
     * @returns {Promise<Object>} Updated todo object
     */
    async updateTodo(todoId, title, description, status = 'pending', dueDate = null, priority = 'medium', tags = [], dependencies = []) {
        try {
            const method = todoId ? 'PUT' : 'POST';
            const url = todoId ? `${this.todosEndpoint}/${todoId}` : this.todosEndpoint;
            
            const response = await fetch(url, {
                method: method,
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    title: title,
                    description: description,
                    status: status,
                    due_date: dueDate,
                    priority: priority,
                    tags: tags,
                    dependencies: dependencies
                })
            });
            
            return await response.json();
        } catch (error) {
            console.error('AgentIntegration: Error updating todo', error);
            throw error;
        }
    }

    /**
     * Assign a todo to an agent
     * @param {string} todoId - Todo ID
     * @param {string} agentId - Agent ID
     * @param {string} assignmentNote - Assignment note
     * @returns {Promise<Object>} Updated todo object
     */
    async assignTodoToAgent(todoId, agentId, assignmentNote = '') {
        try {
            // Add agent assignment metadata to todo
            const todos = await this.readTodos(todoId);
            if (todos.length === 0) {
                throw new Error('Todo not found');
            }
            
            const todo = todos[0];
            const updatedDescription = this._addAgentAssignmentToDescription(todo.description, agentId, assignmentNote);
            
            return await this.updateTodo(
                todoId,
                todo.title,
                updatedDescription,
                todo.status,
                todo.dueDate,
                todo.priority,
                todo.tags,
                todo.dependencies
            );
        } catch (error) {
            console.error('AgentIntegration: Error assigning todo to agent', error);
            throw error;
        }
    }

    /**
     * Add agent assignment to todo description
     * @param {string} description - Original description
     * @param {string} agentId - Agent ID
     * @param {string} assignmentNote - Assignment note
     * @returns {string} Updated description
     */
    _addAgentAssignmentToDescription(description, agentId, assignmentNote) {
        const assignmentText = `\n\n---\n**Agent Assignment:** @${agentId}\n${assignmentNote ? `**Note:** ${assignmentNote}\n` : ''}**Status:** Assigned\n**Timestamp:** ${new Date().toISOString()}`;
        
        if (!description) {
            return assignmentText;
        }
        
        // Check if already assigned to this agent
        if (description.includes(`@${agentId}`)) {
            // Update existing assignment
            return description.replace(/(\n\n---\n\*\*Agent Assignment:\*\* @)[^\n]+/, `$1${agentId}`);
        }
        
        return description + assignmentText;
    }

    /**
     * Create a new todo from natural language
     * @param {string} naturalLanguage - Natural language description
     * @param {string} defaultStatus - Default status
     * @param {string} defaultPriority - Default priority
     * @returns {Promise<Object>} Created todo object
     */
    async createTodoFromNaturalLanguage(naturalLanguage, defaultStatus = 'pending', defaultPriority = 'medium') {
        try {
            // Parse natural language to extract todo details
            const parsed = this._parseNaturalLanguageTodo(naturalLanguage);
            
            return await this.updateTodo(
                null, // New todo
                parsed.title,
                parsed.description,
                defaultStatus,
                parsed.dueDate,
                defaultPriority,
                parsed.tags
            );
        } catch (error) {
            console.error('AgentIntegration: Error creating todo from natural language', error);
            throw error;
        }
    }

    /**
     * Parse natural language todo description
     * @param {string} text - Natural language text
     * @returns {Object} Parsed todo details
     */
    _parseNaturalLanguageTodo(text) {
        if (!text) return { title: 'New Task', description: '' };
        
        // Simple parsing - can be enhanced with NLP
        const result = {
            title: text.split('.')[0].substring(0, 100),
            description: text,
            dueDate: null,
            tags: []
        };
        
        // Extract due date patterns
        const dueDatePatterns = [
            /by (\w+ \d+,? \d{4})/i,
            /by (\d+\/\d+\/\d{2,4})/i,
            /by (\d+\.\d+\.\d{2,4})/i,
            /due (\w+ \d+,? \d{4})/i,
            /due (\d+\/\d+\/\d{2,4})/i,
            /due (\d+\.\d+\.\d{2,4})/i,
            /before (\w+ \d+,? \d{4})/i,
            /(?:on|at) (\w+ \d+,? \d{4})/i
        ];
        
        for (const pattern of dueDatePatterns) {
            const match = text.match(pattern);
            if (match) {
                result.dueDate = match[1];
                break;
            }
        }
        
        // Extract tags
        const tagMatch = text.match(/#(\w+)/g);
        if (tagMatch) {
            result.tags = tagMatch.map(tag => tag.substring(1));
        }
        
        return result;
    }

    // Helper methods
    
    _countWords(text) {
        if (!text) return 0;
        return text.trim().split(/\s+/).length;
    }

    _containsCode(text) {
        if (!text) return false;
        return /```[\s\S]*?```|`[^`]+`|\b(function|class|const|let|var|import|export|return)\b/.test(text);
    }

    _detectLanguage(text) {
        if (!text) return 'plaintext';
        if (/```(python|py)\b[\s\S]*?```/.test(text)) return 'python';
        if (/```(javascript|js)\b[\s\S]*?```/.test(text)) return 'javascript';
        if (/```(html)\b[\s\S]*?```/.test(text)) return 'html';
        if (/```(css)\b[\s\S]*?```/.test(text)) return 'css';
        if (/```(bash|sh)\b[\s\S]*?```/.test(text)) return 'bash';
        if (/<\/?[a-z][\s\S]*>/i.test(text)) return 'html';
        if (/\b(def|class|import)\b/.test(text)) return 'python';
        if (/\b(function|=>|const|let)\b/.test(text)) return 'javascript';
        return 'plaintext';
    }

    _extractSections(text) {
        if (!text) return [];
        const sectionRegex = /^(#+)\s+(.+)$/gm;
        const sections = [];
        let match;
        
        while ((match = sectionRegex.exec(text)) !== null) {
            sections.push({
                level: match[1].length,
                title: match[2].trim(),
                content: ''
            });
        }
        
        return sections;
    }

    _extractBulletPoints(text) {
        if (!text) return [];
        const bulletRegex = /^([\*\-\+]\s+)(.+)$/gm;
        const bullets = [];
        let match;
        
        while ((match = bulletRegex.exec(text)) !== null) {
            bullets.push(match[2].trim());
        }
        
        return bullets;
    }

    _extractKeyValuePairs(text) {
        if (!text) return {};
        const kvRegex = /^([\w\s]+):\s*(.+)$/gm;
        const pairs = {};
        let match;
        
        while ((match = kvRegex.exec(text)) !== null) {
            pairs[match[1].trim()] = match[2].trim();
        }
        
        return pairs;
    }

    _extractCodeBlocks(text) {
        if (!text) return [];
        const codeRegex = /```(\w*)\n([\s\S]*?)```/g;
        const blocks = [];
        let match;
        
        while ((match = codeRegex.exec(text)) !== null) {
            blocks.push({
                language: match[1] || 'text',
                code: match[2].trim()
            });
        }
        
        return blocks;
    }

    _extractQuestions(text) {
        if (!text) return [];
        const questionRegex = /^(?:\w+\s+)?[^\.\?\!]*\?/gm;
        const questions = [];
        let match;
        
        while ((match = questionRegex.exec(text)) !== null) {
            questions.push(match[0].trim());
        }
        
        return questions;
    }

    _daysUntilDue(dueDate) {
        const due = new Date(dueDate);
        const now = new Date();
        const diffTime = due - now;
        return Math.ceil(diffTime / (1000 * 60 * 60 * 24));
    }
}

// Export for use in other modules
if (typeof window !== 'undefined') {
    window.AgentIntegration = AgentIntegration;
} else if (typeof module !== 'undefined' && module.exports) {
    module.exports = AgentIntegration;
}