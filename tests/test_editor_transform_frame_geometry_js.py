import json
import subprocess
import textwrap
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODULE = (ROOT / "static/js/editor/transform-frame-geometry.js").as_uri()
SNAP_MODULE = (ROOT / "static/js/editor/snap.js").as_uri()


def run_node(body: str):
    script = textwrap.dedent(
        f"""
        import {{transformFrameGeometry, hitTestTransformHandle, pointInTransformFrame, resizeTransformFrame}} from {json.dumps(MODULE)};
        {body}
        """
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script], cwd=ROOT,
        text=True, capture_output=True, check=True,
    )
    return json.loads(result.stdout)


def test_frame_geometry_exposes_all_resize_handles_from_one_rotated_frame():
    result = run_node(
        """
        const geometry = transformFrameGeometry({
          centerX: 100,
          centerY: 80,
          width: 120,
          height: 60,
          rotation: 0,
        }, { zoom: 1 });
        console.log(JSON.stringify({
          ids: geometry.resizeHandles.map(handle => handle.id),
          positions: Object.fromEntries(geometry.resizeHandles.map(handle => [handle.id, [handle.x, handle.y]])),
          corners: geometry.corners,
          pivot: geometry.pivot,
        }));
        """
    )
    assert result == {
        "ids": ["tl", "t", "tr", "r", "br", "b", "bl", "l"],
        "positions": {
            "tl": [40, 50],
            "t": [100, 50],
            "tr": [160, 50],
            "r": [160, 80],
            "br": [160, 110],
            "b": [100, 110],
            "bl": [40, 110],
            "l": [40, 80],
        },
        "corners": {
            "tl": {"x": 40, "y": 50},
            "tr": {"x": 160, "y": 50},
            "br": {"x": 160, "y": 110},
            "bl": {"x": 40, "y": 110},
        },
        "pivot": {"x": 100, "y": 80},
    }


def test_frame_geometry_rotates_edges_and_corners_around_the_pivot():
    result = run_node(
        """
        const geometry = transformFrameGeometry({
          centerX: 100,
          centerY: 80,
          width: 120,
          height: 60,
          rotation: 90,
        });
        const rounded = Object.fromEntries(geometry.resizeHandles.map(handle => [
          handle.id,
          [Math.round(handle.x), Math.round(handle.y)],
        ]));
        console.log(JSON.stringify(rounded));
        """
    )
    assert result == {
        "tl": [130, 20],
        "t": [130, 80],
        "tr": [130, 140],
        "r": [100, 140],
        "br": [70, 140],
        "b": [70, 80],
        "bl": [70, 20],
        "l": [100, 20],
    }


def test_hit_testing_uses_every_handle_and_a_larger_invisible_touch_target():
    result = run_node(
        """
        const geometry = transformFrameGeometry({
          centerX: 100,
          centerY: 80,
          width: 120,
          height: 60,
          rotation: 0,
        }, { zoom: 2 });
        console.log(JSON.stringify({
          rotation: [geometry.rotationHandle.x, geometry.rotationHandle.y],
          edge: hitTestTransformHandle(geometry, 160, 80, { zoom: 2, pointerType: 'mouse' }),
          rotationHit: hitTestTransformHandle(geometry, 100, 38, { zoom: 2, pointerType: 'mouse' }),
          mouseMiss: hitTestTransformHandle(geometry, 168, 80, { zoom: 2, pointerType: 'mouse' }),
          touchHit: hitTestTransformHandle(geometry, 168, 80, { zoom: 2, pointerType: 'touch' }),
        }));
        """
    )
    assert result == {
        "rotation": [100, 38],
        "edge": "r",
        "rotationHit": "rot",
        "mouseMiss": None,
        "touchHit": "r",
    }


def test_rotated_frame_interior_rejects_empty_axis_aligned_bounding_box_corners():
    result = run_node(
        """
        const geometry = transformFrameGeometry({
          centerX: 100,
          centerY: 100,
          width: 100,
          height: 100,
          rotation: 45,
        });
        console.log(JSON.stringify({
          center: pointInTransformFrame(geometry, 100, 100),
          visibleTop: pointInTransformFrame(geometry, 100, 40),
          emptyBoundingCorner: pointInTransformFrame(geometry, 35, 35),
        }));
        """
    )
    assert result == {
        "center": True,
        "visibleTop": True,
        "emptyBoundingCorner": False,
    }


def test_transform_cursors_follow_edge_and_corner_screen_direction():
    script = textwrap.dedent(
        f"""
        import {{cursorForHandle}} from {json.dumps(SNAP_MODULE)};
        console.log(JSON.stringify({{
          right0: cursorForHandle('r', 0),
          top0: cursorForHandle('t', 0),
          corner0: cursorForHandle('br', 0),
          right90: cursorForHandle('r', 90),
          corner90: cursorForHandle('br', 90),
        }}));
        """
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script], cwd=ROOT,
        text=True, capture_output=True, check=True,
    )
    assert json.loads(result.stdout) == {
        "right0": "ew-resize",
        "top0": "ns-resize",
        "corner0": "nwse-resize",
        "right90": "ns-resize",
        "corner90": "nesw-resize",
    }


def test_resize_geometry_keeps_opposite_anchor_at_cardinal_and_arbitrary_angles():
    result = run_node(
        """
        const angles=[0,45,90,135,37.25];
        const results=[];
        for (const rotation of angles) {
          const frame={centerX:120,centerY:90,width:100,height:60,rotation};
          const before=transformFrameGeometry(frame);
          const start=before.resizeHandles.find(handle=>handle.id==='r');
          const anchor=before.resizeHandles.find(handle=>handle.id==='l');
          const radians=rotation*Math.PI/180;
          const current={x:start.x+20*Math.cos(radians),y:start.y+20*Math.sin(radians)};
          const resized=resizeTransformFrame(frame,'r',start,current);
          const after=transformFrameGeometry({...resized,rotation});
          const nextAnchor=after.resizeHandles.find(handle=>handle.id==='l');
          results.push({
            rotation,width:resized.width,height:resized.height,
            anchorError:Math.hypot(nextAnchor.x-anchor.x,nextAnchor.y-anchor.y),
          });
        }
        console.log(JSON.stringify(results));
        """
    )
    assert [entry["rotation"] for entry in result] == [0, 45, 90, 135, 37.25]
    for entry in result:
        assert entry["width"] == 120
        assert entry["height"] == 60
        assert entry["anchorError"] < 1e-9


def test_shift_alt_corner_resize_is_centered_and_aspect_locked():
    result = run_node(
        """
        const frame={centerX:50,centerY:25,width:100,height:50,rotation:0};
        const geometry=transformFrameGeometry(frame);
        const start=geometry.resizeHandles.find(handle=>handle.id==='br');
        const resized=resizeTransformFrame(frame,'br',start,{x:start.x+30,y:start.y+10},{
          centered:true,lockAspect:true,
        });
        console.log(JSON.stringify(resized));
        """
    )
    assert result == {
        "width": 160,
        "height": 80,
        "centerX": 50,
        "centerY": 25,
        "flipH": False,
        "flipV": False,
    }
