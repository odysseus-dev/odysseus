/**
 * Test script to verify click handlers for sidebar-skills-btn and sidebar-memory-btn
 * Run this in the browser console on the Odysseus application page
 */

(function testButtonHandlers() {
  const results = {
    issues: [],
    warnings: [],
    ok: []
  };

  // 1. Check if buttons exist
  const memoryBtn = document.getElementById('sidebar-memory-btn');
  const skillsBtn = document.getElementById('sidebar-skills-btn');

  if (!memoryBtn) {
    results.issues.push('CRITICAL: sidebar-memory-btn element not found in DOM');
  } else {
    results.ok.push('sidebar-memory-btn element exists');
  }

  if (!skillsBtn) {
    results.issues.push('CRITICAL: sidebar-skills-btn element not found in DOM');
  } else {
    results.ok.push('sidebar-skills-btn element exists');
  }

  // 2. Check for duplicate IDs on skills button
  if (skillsBtn) {
    const skillsBtnHtml = skillsBtn.outerHTML;
    const idMatches = skillsBtnHtml.match(/id=/g);
    if (idMatches && idMatches.length > 1) {
      results.issues.push(`CRITICAL: sidebar-skills-btn has ${idMatches.length} id attributes! HTML: ${skillsBtnHtml.substring(0, 100)}...`);
    }
  }

  // 3. Check accessibility attributes
  if (memoryBtn) {
    if (!memoryBtn.hasAttribute('tabindex')) {
      results.warnings.push('sidebar-memory-btn missing tabindex attribute (should be enhanced by a11y.js)');
    } else {
      results.ok.push(`sidebar-memory-btn has tabindex="${memoryBtn.getAttribute('tabindex')}"`);
    }

    if (!memoryBtn.hasAttribute('role')) {
      results.warnings.push('sidebar-memory-btn missing role attribute (should be enhanced by a11y.js)');
    } else {
      results.ok.push(`sidebar-memory-btn has role="${memoryBtn.getAttribute('role')}"`);
    }
  }

  if (skillsBtn) {
    if (!skillsBtn.hasAttribute('tabindex')) {
      results.warnings.push('sidebar-skills-btn missing tabindex attribute (should be enhanced by a11y.js)');
    } else {
      results.ok.push(`sidebar-skills-btn has tabindex="${skillsBtn.getAttribute('tabindex')}"`);
    }

    if (!skillsBtn.hasAttribute('role')) {
      results.warnings.push('sidebar-skills-btn missing role attribute (should be enhanced by a11y.js)');
    } else {
      results.ok.push(`sidebar-skills-btn has role="${skillsBtn.getAttribute('role')}"`);
    }
  }

  // 4. Check for click event listeners
  // Note: This uses a heuristic approach since we can't directly inspect listeners
  const checkClickHandler = (btn, name) => {
    if (!btn) return;

    // Check for onclick attribute
    if (btn.hasAttribute('onclick')) {
      results.ok.push(`${name} has inline onclick attribute`);
    }

    // Check if element is marked as a11y enhanced (indicates keyboard handler attached)
    if (btn.dataset.a11yEnhanced === '1') {
      results.ok.push(`${name} is a11y enhanced (keyboard navigation should work)`);
    } else {
      results.warnings.push(`${name} is NOT a11y enhanced yet - may need to wait for a11y.js`);
    }

    // Check data-a11y-activatable
    if (btn.hasAttribute('data-a11y-activatable')) {
      results.ok.push(`${name} has data-a11y-activatable attribute`);
    }
  };

  checkClickHandler(memoryBtn, 'sidebar-memory-btn');
  checkClickHandler(skillsBtn, 'sidebar-skills-btn');

  // 5. Check for modal elements
  const memoryModal = document.getElementById('memory-modal');
  const memoryManagementModal = document.getElementById('memory-management-modal');

  if (!memoryModal) {
    results.warnings.push('memory-modal element not found (used by init.js lines 100-110)');
  } else {
    results.ok.push('memory-modal element exists');
  }

  if (!memoryManagementModal) {
    results.warnings.push('memory-management-modal element not found (used by init.js lines 468+)');
  } else {
    results.ok.push('memory-management-modal element exists');
  }

  // 6. Check for tab elements
  const browseTab = document.querySelector('.memory-tab[data-memory-tab="browse"]');
  const skillsTab = document.querySelector('.memory-tab[data-memory-tab="skills"]');
  const browseTabById = document.getElementById('browse-tab');
  const skillsTabById = document.getElementById('skills-tab');

  if (!browseTab && !browseTabById) {
    results.issues.push('Browse tab not found (neither by data-memory-tab="browse" nor id="browse-tab")');
  } else {
    results.ok.push(`Browse tab found: ${browseTab ? 'by data-memory-tab' : ''} ${browseTabById ? 'by id' : ''}`);
  }

  if (!skillsTab && !skillsTabById) {
    results.issues.push('Skills tab not found (neither by data-memory-tab="skills" nor id="skills-tab")');
  } else {
    results.ok.push(`Skills tab found: ${skillsTab ? 'by data-memory-tab' : ''} ${skillsTabById ? 'by id' : ''}`);
  }

  // 7. Check for conflicting event handlers in init.js
  // The init.js has TWO sets of handlers:
  // - Lines 100-107: Uses memory-modal and querySelector('.memory-tab[...]')
  // - Lines 466-478: Uses memory-management-modal and getElementById('...-tab')

  results.warnings.push('init.js has TWO sets of event handlers for these buttons (lines ~100 and ~466)');
  results.warnings.push('First handler uses memory-modal + querySelector, second uses memory-management-modal + getElementById');

  // 8. Check if a11y.js is loaded
  const a11yScript = document.querySelector('script[src*="a11y.js"]');
  if (!a11yScript) {
    results.warnings.push('a11y.js script not found in DOM - keyboard navigation may not work');
  } else {
    results.ok.push('a11y.js script is loaded');
  }

  // Print results
  console.log('=== Button Handler Test Results ===\n');

  if (results.issues.length > 0) {
    console.log('❌ CRITICAL ISSUES:');
    results.issues.forEach(issue => console.log('  - ' + issue));
    console.log('');
  }

  if (results.warnings.length > 0) {
    console.log('⚠️ WARNINGS:');
    results.warnings.forEach(warning => console.log('  - ' + warning));
    console.log('');
  }

  if (results.ok.length > 0) {
    console.log('✅ OK:');
    results.ok.forEach(ok => console.log('  - ' + ok));
    console.log('');
  }

  return results;
})();
