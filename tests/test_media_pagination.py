# tests/test_media_pagination.py
#
# Copyright (c) 2025-2026 Cindy's World LLC and contributors
# Licensed under the MIT License. See LICENSE.md for details.
#
"""
Tests for Media Editor pagination, search, and filtering functionality.

This test suite covers:
- Pagination navigation (Testing Recommendation #2)
- Search functionality (Testing Recommendation #3)
- Media type filtering (Testing Recommendation #4)
- Combined filters (Testing Recommendation #5)
- Edge cases (Testing Recommendation #6)
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
from pathlib import Path
import json

# Import the media blueprint and helper functions
from admin_console.media import media_bp, api_media_list
from admin_console.helpers import is_state_media_directory


class TestMediaPagination:
    """Test pagination functionality"""
    
    def test_default_pagination_parameters(self, client):
        """Test that default pagination parameters are applied correctly"""
        # Arrange: Mock the directory to return some test data
        with patch('admin_console.media.resolve_media_path') as mock_resolve:
            mock_resolve.return_value = Path('/test/media')
            with patch('admin_console.media.is_state_media_directory', return_value=False):
                with patch('pathlib.Path.exists', return_value=True):
                    with patch('pathlib.Path.glob', return_value=[]):
                        # Act: Call without pagination parameters
                        response = client.get('/admin/api/media?directory=/test/media')
                        
                        # Assert: Check response structure
                        assert response.status_code == 200
                        data = json.loads(response.data)
                        assert 'pagination' in data
                        assert data['pagination']['page'] == 1
                        assert data['pagination']['page_size'] == 10
    
    def test_custom_page_and_page_size(self, client):
        """Test custom page and page_size parameters"""
        with patch('admin_console.media.resolve_media_path') as mock_resolve:
            mock_resolve.return_value = Path('/test/media')
            with patch('admin_console.media.is_state_media_directory', return_value=False):
                with patch('pathlib.Path.exists', return_value=True):
                    with patch('pathlib.Path.glob', return_value=[]):
                        # Act: Call with custom pagination
                        response = client.get('/admin/api/media?directory=/test/media&page=2&page_size=20')
                        
                        # Assert
                        assert response.status_code == 200
                        data = json.loads(response.data)
                        assert data['pagination']['page'] == 2
                        assert data['pagination']['page_size'] == 20
    
    def test_page_size_capped_at_100(self, client):
        """Test that page_size is capped at 100 to prevent abuse"""
        with patch('admin_console.media.resolve_media_path') as mock_resolve:
            mock_resolve.return_value = Path('/test/media')
            with patch('admin_console.media.is_state_media_directory', return_value=False):
                with patch('pathlib.Path.exists', return_value=True):
                    with patch('pathlib.Path.glob', return_value=[]):
                        # Act: Try to set page_size > 100
                        response = client.get('/admin/api/media?directory=/test/media&page_size=500')
                        
                        # Assert: Should be capped at 100
                        assert response.status_code == 200
                        data = json.loads(response.data)
                        assert data['pagination']['page_size'] == 100
    
    def test_invalid_page_number_defaults_to_1(self, client):
        """Test that invalid page numbers default to 1"""
        with patch('admin_console.media.resolve_media_path') as mock_resolve:
            mock_resolve.return_value = Path('/test/media')
            with patch('admin_console.media.is_state_media_directory', return_value=False):
                with patch('pathlib.Path.exists', return_value=True):
                    with patch('pathlib.Path.glob', return_value=[]):
                        # Act: Try invalid page numbers
                        response = client.get('/admin/api/media?directory=/test/media&page=0')
                        data = json.loads(response.data)
                        assert data['pagination']['page'] == 1
                        
                        response = client.get('/admin/api/media?directory=/test/media&page=-5')
                        data = json.loads(response.data)
                        assert data['pagination']['page'] == 1
                        
                        response = client.get('/admin/api/media?directory=/test/media&page=invalid')
                        data = json.loads(response.data)
                        assert data['pagination']['page'] == 1


class TestMediaSearch:
    """Test search functionality across media fields"""
    
    def test_search_by_unique_id(self, client):
        """Test searching by unique_id"""
        # This would need actual test data setup
        # For now, we'll verify the parameter is passed correctly
        with patch('admin_console.media.resolve_media_path') as mock_resolve:
            mock_resolve.return_value = Path('/test/media')
            with patch('admin_console.media.is_state_media_directory', return_value=False):
                with patch('pathlib.Path.exists', return_value=True):
                    with patch('pathlib.Path.glob', return_value=[]):
                        response = client.get('/admin/api/media?directory=/test/media&search=test_id_123')
                        
                        assert response.status_code == 200
                        data = json.loads(response.data)
                        assert data['pagination']['search'] == 'test_id_123'
    
    def test_search_preserves_pagination(self, client):
        """Test that search results are paginated correctly"""
        with patch('admin_console.media.resolve_media_path') as mock_resolve:
            mock_resolve.return_value = Path('/test/media')
            with patch('admin_console.media.is_state_media_directory', return_value=False):
                with patch('pathlib.Path.exists', return_value=True):
                    with patch('pathlib.Path.glob', return_value=[]):
                        response = client.get('/admin/api/media?directory=/test/media&search=test&page=2&page_size=5')
                        
                        assert response.status_code == 200
                        data = json.loads(response.data)
                        assert data['pagination']['search'] == 'test'
                        assert data['pagination']['page'] == 2
                        assert data['pagination']['page_size'] == 5
    
    def test_empty_search_treated_as_no_search(self, client):
        """Test that empty/whitespace search is treated as no search"""
        with patch('admin_console.media.resolve_media_path') as mock_resolve:
            mock_resolve.return_value = Path('/test/media')
            with patch('admin_console.media.is_state_media_directory', return_value=False):
                with patch('pathlib.Path.exists', return_value=True):
                    with patch('pathlib.Path.glob', return_value=[]):
                        response = client.get('/admin/api/media?directory=/test/media&search=   ')
                        
                        assert response.status_code == 200
                        data = json.loads(response.data)
                        assert data['pagination']['search'] is None


class TestMediaTypeFilter:
    """Test media type filtering"""
    
    @pytest.mark.parametrize('media_type', [
        'all', 'stickers', 'emoji', 'video', 'photos', 'audio', 'other'
    ])
    def test_valid_media_types(self, client, media_type):
        """Test all valid media type values"""
        with patch('admin_console.media.resolve_media_path') as mock_resolve:
            mock_resolve.return_value = Path('/test/media')
            with patch('admin_console.media.is_state_media_directory', return_value=False):
                with patch('pathlib.Path.exists', return_value=True):
                    with patch('pathlib.Path.glob', return_value=[]):
                        response = client.get(f'/admin/api/media?directory=/test/media&media_type={media_type}')
                        
                        assert response.status_code == 200
                        data = json.loads(response.data)
                        if media_type == 'all':
                            assert data['pagination']['media_type'] is None
                        else:
                            assert data['pagination']['media_type'] == media_type
    
    def test_invalid_media_type_defaults_to_all(self, client):
        """Test that invalid media_type defaults to 'all'"""
        with patch('admin_console.media.resolve_media_path') as mock_resolve:
            mock_resolve.return_value = Path('/test/media')
            with patch('admin_console.media.is_state_media_directory', return_value=False):
                with patch('pathlib.Path.exists', return_value=True):
                    with patch('pathlib.Path.glob', return_value=[]):
                        response = client.get('/admin/api/media?directory=/test/media&media_type=invalid_type')
                        
                        assert response.status_code == 200
                        data = json.loads(response.data)
                        assert data['pagination']['media_type'] is None  # 'all' is returned as None
    
    def test_stickers_vs_emoji_distinction(self, client):
        """Test that stickers and emoji are distinguished correctly"""
        # This test would need actual database/filesystem setup with test data
        # For now, we verify the parameters are handled correctly
        with patch('admin_console.media.resolve_media_path') as mock_resolve:
            mock_resolve.return_value = Path('/test/media')
            with patch('admin_console.media.is_state_media_directory', return_value=False):
                with patch('pathlib.Path.exists', return_value=True):
                    with patch('pathlib.Path.glob', return_value=[]):
                        # Test stickers filter
                        response = client.get('/admin/api/media?directory=/test/media&media_type=stickers')
                        assert response.status_code == 200
                        data = json.loads(response.data)
                        assert data['pagination']['media_type'] == 'stickers'
                        
                        # Test emoji filter
                        response = client.get('/admin/api/media?directory=/test/media&media_type=emoji')
                        assert response.status_code == 200
                        data = json.loads(response.data)
                        assert data['pagination']['media_type'] == 'emoji'


class TestCombinedFilters:
    """Test combinations of limit, search, media_type, and pagination"""
    
    def test_limit_plus_search_plus_media_type(self, client):
        """Test all filters working together"""
        with patch('admin_console.media.resolve_media_path') as mock_resolve:
            mock_resolve.return_value = Path('/test/media')
            with patch('admin_console.media.is_state_media_directory', return_value=False):
                with patch('pathlib.Path.exists', return_value=True):
                    with patch('pathlib.Path.glob', return_value=[]):
                        response = client.get(
                            '/admin/api/media?directory=/test/media'
                            '&limit=100&search=dancing&media_type=stickers&page=2&page_size=15'
                        )
                        
                        assert response.status_code == 200
                        data = json.loads(response.data)
                        assert data['pagination']['limit'] == 100
                        assert data['pagination']['search'] == 'dancing'
                        assert data['pagination']['media_type'] == 'stickers'
                        assert data['pagination']['page'] == 2
                        assert data['pagination']['page_size'] == 15
    
    def test_filter_processing_order(self, client):
        """Test that filters are applied in correct order: limit → media_type → search → paginate"""
        # This is more of an integration test that would need actual data
        # The order is documented in the docstring and implementation
        # We verify the parameters are all handled
        with patch('admin_console.media.resolve_media_path') as mock_resolve:
            mock_resolve.return_value = Path('/test/media')
            with patch('admin_console.media.is_state_media_directory', return_value=False):
                with patch('pathlib.Path.exists', return_value=True):
                    with patch('pathlib.Path.glob', return_value=[]):
                        response = client.get(
                            '/admin/api/media?directory=/test/media&limit=50&media_type=video&search=cat'
                        )
                        
                        assert response.status_code == 200
                        data = json.loads(response.data)
                        # All filters should be reflected in pagination metadata
                        assert data['pagination']['limit'] == 50
                        assert data['pagination']['media_type'] == 'video'
                        assert data['pagination']['search'] == 'cat'


class TestEdgeCases:
    """Test edge cases and error conditions"""
    
    def test_empty_results_with_filters(self, client):
        """Test that empty results are handled correctly with filters active"""
        with patch('admin_console.media.resolve_media_path') as mock_resolve:
            mock_resolve.return_value = Path('/test/media')
            with patch('admin_console.media.is_state_media_directory', return_value=False):
                with patch('pathlib.Path.exists', return_value=True):
                    with patch('pathlib.Path.glob', return_value=[]):
                        response = client.get(
                            '/admin/api/media?directory=/test/media&search=nonexistent_media_xyz_123'
                        )
                        
                        assert response.status_code == 200
                        data = json.loads(response.data)
                        assert data['media_files'] == []
                        assert data['pagination']['total_items'] == 0
                        assert data['pagination']['total_pages'] == 0
    
    def test_single_page_of_results(self, client):
        """Test handling when all results fit on one page"""
        with patch('admin_console.media.resolve_media_path') as mock_resolve:
            mock_resolve.return_value = Path('/test/media')
            with patch('admin_console.media.is_state_media_directory', return_value=False):
                with patch('pathlib.Path.exists', return_value=True):
                    # Mock 5 files (less than default page_size of 10)
                    mock_files = [Path(f'/test/media/file{i}.json') for i in range(5)]
                    with patch('pathlib.Path.glob', return_value=mock_files):
                        with patch('admin_console.media.get_directory_media_source') as mock_source:
                            mock_cache = Mock()
                            mock_cache.get_cached_record.return_value = {
                                'kind': 'photo',
                                'description': 'Test photo',
                                'mime_type': 'image/jpeg'
                            }
                            mock_source.return_value = mock_cache
                            
                            with patch('admin_console.media.find_media_file', return_value=None):
                                with patch('admin_console.media.CompositeMediaSource'):
                                    response = client.get('/admin/api/media?directory=/test/media')
                                    
                                    assert response.status_code == 200
                                    data = json.loads(response.data)
                                    assert data['pagination']['total_pages'] in [0, 1]
                                    assert data['pagination']['has_more'] is False
    
    def test_missing_directory_parameter(self, client):
        """Test error handling when directory parameter is missing"""
        response = client.get('/admin/api/media')
        
        assert response.status_code == 400
        data = json.loads(response.data)
        assert 'error' in data
        assert 'directory' in data['error'].lower()
    
    def test_nonexistent_directory(self, client):
        """Test error handling for nonexistent directory"""
        with patch('admin_console.media.resolve_media_path') as mock_resolve:
            mock_resolve.return_value = Path('/nonexistent/path')
            with patch('pathlib.Path.exists', return_value=False):
                response = client.get('/admin/api/media?directory=/nonexistent/path')
                
                assert response.status_code == 404
                data = json.loads(response.data)
                assert 'error' in data


@pytest.fixture
def client():
    """Create a test client for the Flask app"""
    from flask import Flask
    app = Flask(__name__)
    app.register_blueprint(media_bp, url_prefix='/admin')
    app.config['TESTING'] = True
    
    with app.test_client() as client:
        yield client
