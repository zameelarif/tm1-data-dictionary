"""The output scan finds the files, databases and commands a process sends data to."""

from __future__ import annotations

from tm1_data_dictionary.parser.blocks import CodeLine
from tm1_data_dictionary.parser.output_scan import (
    RUNTIME_PREFIX,
    UNRECOGNISED_TARGET,
    OutputCall,
    OutputScanner,
    resolve_text,
)
from tm1_data_dictionary.parser.var_trace import Env


def _scan(
    prolog: str = "",
    data: str = "",
    *,
    parameters: tuple[str, ...] = (),
    variables: tuple[str, ...] = (),
) -> list[OutputCall]:
    lines: list[CodeLine] = []
    for block, text in (("Prolog", prolog), ("Data", data)):
        for n, raw in enumerate(text.strip().splitlines(), start=1):
            if raw.strip():
                lines.append(CodeLine(block=block, line_no=n, raw=raw, code=raw.strip()))
    scanner = OutputScanner("P", source_variables=variables, parameters=parameters)
    return scanner.scan(lines)


def _targets(calls: list[OutputCall]) -> list[tuple[str, str, str]]:
    return [(c.output_type, c.target, c.resolution) for c in calls]


# --------------------------------------------------------------------------- files
def test_literal_file_name() -> None:
    calls = _scan("ASCIIOutput('D:\\Export\\GL.csv', 'Account', 'Value');")
    assert _targets(calls) == [("File", "D:\\Export\\GL.csv", "Literal")]
    call = calls[0]
    assert (call.function, call.block, call.line_no) == ("ASCIIOutput", "Prolog", 1)
    assert call.detail == "2 field(s): 'Account', 'Value'"
    assert call.expression == "'D:\\Export\\GL.csv'"


def test_text_output_is_a_file_too() -> None:
    assert _targets(_scan("TextOutput('x.txt', 'a');")) == [("File", "x.txt", "Literal")]


def test_path_built_in_the_prolog_is_found_where_it_is_used() -> None:
    prolog = "sDir = 'D:\\Export\\';\nsFile = sDir | 'GL.csv';"
    data = "ASCIIOutput(sFile, vAccount, vValue);"
    calls = _scan(prolog, data)
    assert _targets(calls) == [("File", "D:\\Export\\GL.csv", "Resolved")]
    assert (calls[0].block, calls[0].line_no) == ("Data", 1)
    assert calls[0].detail == "2 field(s): vAccount, vValue"


def test_file_name_with_a_parameter_keeps_the_known_parts() -> None:
    prolog = "sFile = 'D:\\Export\\GL_' | pYear | '.csv';\nASCIIOutput(sFile, 'x');"
    calls = _scan(prolog, parameters=("pYear",))
    assert _targets(calls) == [("File", "D:\\Export\\GL_<pYear>.csv", "Partly")]
    assert calls[0].detail.startswith("Parameter: ")


def test_a_path_built_through_two_variables_still_shows_the_known_parts() -> None:
    prolog = (
        "sBase = 'D:\\Export\\' | pYear | '\\';\n"
        "sFile = sBase | 'GL.csv';\n"
        "ASCIIOutput(sFile, 'x');"
    )
    calls = _scan(prolog, parameters=("pYear",))
    assert _targets(calls) == [("File", "D:\\Export\\<pYear>\\GL.csv", "Partly")]


def test_a_variable_that_refers_to_itself_does_not_loop_and_stays_runtime() -> None:
    prolog = "sFile = pStart;\nsFile = sFile | '.csv';\nASCIIOutput(sFile, 'x');"
    calls = _scan(prolog, parameters=("pStart",))
    assert [(c.output_type, c.resolution) for c in calls] == [("File", "Runtime")]


def test_a_name_looked_up_from_a_cube_is_runtime_and_labelled_mapped() -> None:
    prolog = "sFile = CellGetS('System Info', 'Export Path', 'String');\nASCIIOutput(sFile, 'x');"
    calls = _scan(prolog)
    assert _targets(calls) == [("File", RUNTIME_PREFIX + "sFile", "Runtime")]
    assert calls[0].detail.startswith("Mapped: ")


def test_a_source_variable_in_the_name_is_labelled() -> None:
    calls = _scan("ASCIIOutput('out_' | vRegion | '.csv', vValue);", variables=("vRegion",))
    assert _targets(calls) == [("File", "out_<vRegion>.csv", "Partly")]
    assert calls[0].detail.startswith("SourceVariable: ")


def test_a_target_that_is_only_a_parameter_is_runtime_not_dropped() -> None:
    calls = _scan("ASCIIOutput(pFile, 'x');", parameters=("pFile",))
    assert _targets(calls) == [("File", RUNTIME_PREFIX + "pFile", "Runtime")]
    assert calls[0].detail.startswith("Parameter: pFile")


def test_both_branches_of_an_if_give_one_row_each() -> None:
    prolog = (
        "If(pMode = 1);\n  sFile = 'a.csv';\nElse;\n  sFile = 'b.csv';\nEndIf;\n"
        "ASCIIOutput(sFile, 'x');"
    )
    calls = _scan(prolog, parameters=("pMode",))
    assert sorted(_targets(calls)) == [
        ("File", "a.csv", "Resolved"),
        ("File", "b.csv", "Resolved"),
    ]


def test_a_branch_with_an_unknown_part_does_not_hide_the_other_branch() -> None:
    prolog = (
        "If(pMode @= 'FULL');\n  sFile = 'D:\\Full_' | pYear | '.csv';\n"
        "Else;\n  sFile = 'D:\\Delta.csv';\nEndIf;\nASCIIOutput(sFile, 'x');"
    )
    calls = _scan(prolog, parameters=("pMode", "pYear"))
    assert sorted(_targets(calls)) == [
        ("File", "D:\\Delta.csv", "Resolved"),
        ("File", "D:\\Full_<pYear>.csv", "Partly"),
    ]


def test_a_new_unconditional_assignment_forgets_the_earlier_branches() -> None:
    prolog = (
        "If(pMode = 1);\n  sFile = 'a_' | pYear;\nElse;\n  sFile = 'b.csv';\nEndIf;\n"
        "sFile = 'final.csv';\nASCIIOutput(sFile, 'x');"
    )
    assert _targets(_scan(prolog, parameters=("pMode", "pYear"))) == [
        ("File", "final.csv", "Resolved")
    ]


def test_branches_that_are_all_unknown_stay_one_runtime_row() -> None:
    prolog = "If(pMode = 1);\n  sFile = pA;\nElse;\n  sFile = pB;\nEndIf;\nASCIIOutput(sFile, 'x');"
    calls = _scan(prolog, parameters=("pMode", "pA", "pB"))
    assert _targets(calls) == [("File", RUNTIME_PREFIX + "sFile", "Runtime")]


def test_a_variable_reused_for_two_files_resolves_per_line() -> None:
    prolog = (
        "sFile = 'one.csv';\nASCIIOutput(sFile, 'x');\nsFile = 'two.csv';\nASCIIOutput(sFile, 'y');"
    )
    assert [c.target for c in _scan(prolog)] == ["one.csv", "two.csv"]


def test_process_name_in_a_file_name() -> None:
    calls = _scan("ASCIIOutput('log_' | GetProcessName() | '.txt', 'x');")
    assert [c.target for c in calls] == ["log_P.txt"]


def test_no_fields_and_many_fields() -> None:
    assert _scan("ASCIIOutput('a.csv');")[0].detail == "no fields"
    many = _scan("ASCIIOutput('a.csv', 'a','b','c','d','e','f','g','h');")[0].detail
    assert many == "8 field(s): 'a', 'b', 'c', 'd', 'e', 'f', ..."


# --------------------------------------------------------------------------- ODBC / command
def test_odbc_output_target_is_the_dsn_and_the_table_is_the_detail() -> None:
    sql = "'INSERT INTO dbo.GL (Acct, Val) VALUES (' | vAcct | ')'"
    calls = _scan(f"ODBCOutput('Warehouse', {sql});", variables=("vAcct",))
    assert _targets(calls) == [("ODBC", "Warehouse", "Literal")]
    assert calls[0].detail.startswith("INSERT INTO dbo.GL | INSERT INTO dbo.GL")


def test_odbc_statements_without_a_known_verb_keep_the_sql() -> None:
    calls = _scan("ODBCOutput('Warehouse', 'EXEC refresh_gl');")
    assert calls[0].detail == "SQL: EXEC refresh_gl"


def test_execute_command_target_is_the_command() -> None:
    calls = _scan("ExecuteCommand('copy D:\\a.csv E:\\b.csv', 1);")
    assert _targets(calls) == [("Command", "copy D:\\a.csv E:\\b.csv", "Literal")]
    assert calls[0].detail == "wait=1"


def test_command_built_from_a_path_variable() -> None:
    prolog = "sFile = 'D:\\a.csv';\nExecuteCommand('copy ' | sFile | ' E:\\', 0);"
    assert _targets(_scan(prolog)) == [("Command", "copy D:\\a.csv E:\\", "Resolved")]


# --------------------------------------------------------------------------- Bedrock
def _bedrock(process: str, params: str) -> str:
    return f"ExecuteProcess('{process}', 'pCube', 'Sales', {params});"


def test_bedrock_export_joins_directory_and_file() -> None:
    code = _bedrock("Bedrock.Cube.Data.Export", "'pTgtDir', 'D:\\Export', 'pTgtFile', 'sales.csv'")
    calls = _scan(code)
    assert _targets(calls) == [("File", "D:\\Export\\sales.csv", "Literal")]
    assert calls[0].function == "Bedrock.Cube.Data.Export"


def test_bedrock_4_name_and_variable_parts() -> None:
    code = "sDir = 'D:\\Export\\';\n" + _bedrock(
        "}bedrock.cube.data.export", "'pTargetDir', sDir, 'pFileName', 'x_' | pYear | '.csv'"
    )
    calls = _scan(code, parameters=("pYear",))
    assert _targets(calls) == [("File", "D:\\Export\\x_<pYear>.csv", "Partly")]


def test_bedrock_with_only_a_file_parameter() -> None:
    calls = _scan(_bedrock("Bedrock.Cube.Data.Export", "'pFile', 'x.csv'"))
    assert _targets(calls) == [("File", "x.csv", "Literal")]


def test_bedrock_with_an_unknown_directory_is_partly_known() -> None:
    code = _bedrock("Bedrock.Cube.Data.Export", "'pTgtDir', pDir, 'pTgtFile', 'x.csv'")
    assert _targets(_scan(code, parameters=("pDir",))) == [("File", "<pDir>\\x.csv", "Partly")]


def test_bedrock_with_nothing_known_is_runtime() -> None:
    code = _bedrock("Bedrock.Cube.Data.Export", "'pTgtDir', pDir, 'pTgtFile', pFile")
    calls = _scan(code, parameters=("pDir", "pFile"))
    assert _targets(calls) == [("File", RUNTIME_PREFIX + "pDir + pFile", "Runtime")]


def test_bedrock_with_unrecognised_parameters_is_still_recorded() -> None:
    calls = _scan(_bedrock("Bedrock.Cube.Data.Export", "'pOther', 'x'"))
    assert _targets(calls) == [("File", UNRECOGNISED_TARGET, "Runtime")]
    assert "pother='x'" in calls[0].detail


def test_other_processes_and_functions_are_ignored() -> None:
    code = "ExecuteProcess('Some.Other', 'pFile', 'x.csv');\nCellPutN(1, 'Sales', 'a');"
    assert _scan(code) == []
    assert _scan("ExecuteProcess(sName, 'pFile', 'x');", parameters=("sName",)) == []


def test_repeated_calls_are_all_reported_with_their_lines() -> None:
    prolog = "ASCIIOutput('a.csv', 'x');\nASCIIOutput('a.csv', 'y');"
    calls = _scan(prolog)
    assert [(c.target, c.line_no) for c in calls] == [("a.csv", 1), ("a.csv", 2)]


# --------------------------------------------------------------------------- resolve_text
def test_resolve_text_limits_the_number_of_values() -> None:
    env = Env(process_name="P")
    for i in range(30):
        env.assign("v", f"'x{i}'")
        env.values["v"] = frozenset(f"x{i}" for i in range(30))
    assert len(resolve_text(env, "v")) == 12
