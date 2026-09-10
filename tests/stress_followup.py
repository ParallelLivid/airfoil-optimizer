"""Follow-up probes for publication stress findings; pass the evidence directory."""
import json
from pathlib import Path
import sys
import time
import numpy as np
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import analysis
import config
import xfoil_runner as runner


if __name__ == '__main__':
    evidence = Path(sys.argv[1]).resolve()
    assert evidence.is_relative_to(ROOT)
    try:
        config.validate_simulation_inputs(300000, 0, .002, .0001)
    except ValueError:
        precision_rejected = True
    else:
        precision_rejected = False
    assert precision_rejected
    before = time.monotonic()
    result = runner._simulate('800_with_60s_budget', 300000, 0, .799, .001, evidence/'max800_followup',
                              {'panel_nodes':160, 'max_iterations':100, 'timeout_seconds':60}, naca_code='2412')
    result.pop('polar_data', None)
    result['seconds'] = time.monotonic()-before
    result['output_dir'] = str(Path(result['output_dir']).relative_to(ROOT))
    assert result['status'] == 'complete' and result['converged_points'] == 800
    record = {'subprecision_input_rejected': precision_rejected, 'max800':result}
    (evidence/'followup.json').write_text(json.dumps(record, indent=2))
    print(json.dumps(record, indent=2))
