"""Review schema fragments shared by composition and the final gate."""
from .policy import PolicyError

SECTIONS = ('reviewed_stage', 'counterexamples', 'observations', 'unresolved')


def validate_section(section, value, prior_stage):
    if section == 'reviewed_stage':
        valid = value == prior_stage
        rule = 'must equal ' + repr(prior_stage)
    elif section == 'counterexamples':
        valid = (isinstance(value, list) and bool(value) and
                 all(isinstance(case, str) and case.strip() for case in value))
        rule = 'must contain at least one nonempty ATTEMPTED test case even when no defect is found'
    elif section in ('observations', 'unresolved'):
        valid = isinstance(value, list)
        rule = 'must be a list'
    else:
        valid = False
        rule = 'unknown section; choose ' + ', '.join(SECTIONS)
    if not valid:
        raise PolicyError('REVIEW section ' + str(section) + ': ' + rule +
                          '. Rewrite only this section using write data={"<section>": <value>}.')


def validate_document(report, prior_stage):
    if not isinstance(report, dict):
        raise PolicyError('REVIEW document must be an object')
    for section in SECTIONS:
        validate_section(section, report.get(section), prior_stage)
