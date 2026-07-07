import unittest
from unittest.mock import MagicMock, patch
from src.rag_manager import RAGManager

class TestRAGManagerSearchSignature(unittest.TestCase):
    @patch('src.rag_manager.VectorRAG')
    def test_search_signature_accepts_owner(self, mock_vector_rag_class):
        # Create a mock instance for VectorRAG
        mock_vector_rag = MagicMock()
        mock_vector_rag_class.return_value = mock_vector_rag

        # Initialize RAGManager
        manager = RAGManager()

        # Test call with owner parameter
        manager.search("test query", k=3, owner="user1")

        # Verify that search was called on the underlying vector_rag with the correct parameters
        mock_vector_rag.search.assert_called_once_with("test query", 3, owner="user1")

if __name__ == '__main__':
    unittest.main()
