from app.ports import (
    AUTO_PORTS,
    INVALID_PORT_MESSAGE,
    automatic_port,
    is_valid_custom_port,
    select_port_interactive,
)


def test_custom_port_validation_is_exactly_four_digits():
    assert is_valid_custom_port("1000")
    assert is_valid_custom_port("8080")
    assert is_valid_custom_port("9999")
    for value in ("999", "10000", "abcd", " 8080", "0x10"):
        assert not is_valid_custom_port(value)


def test_automatic_ports_are_checked_in_required_order_without_duplicates():
    calls = []
    winner = 9090

    def checker(host, port):
        calls.append((host, port))
        return port == winner

    assert automatic_port("0.0.0.0", checker) == winner
    expected = []
    for port in AUTO_PORTS:
        if port not in expected:
            expected.append(port)
        if port == winner:
            break
    assert [port for _, port in calls] == expected


def test_automatic_selection_continues_through_remaining_range():
    listed = set(AUTO_PORTS)
    winner = next(port for port in range(1000, 10000) if port not in listed)
    assert automatic_port("127.0.0.1", lambda _host, port: port == winner) == winner


def test_default_occupied_uses_automatic_selection():
    output = []
    answers = iter(["d"])
    result = select_port_interactive(
        "0.0.0.0",
        input_fn=lambda _prompt: next(answers),
        print_fn=output.append,
        checker=lambda _host, port: port == 1010,
    )
    assert result == 1010
    assert any("occupied" in line for line in output)


def test_invalid_custom_port_prints_required_message_then_accepts_valid():
    answers = iter(["n", "123", "9090"])
    output = []
    result = select_port_interactive(
        "0.0.0.0",
        input_fn=lambda _prompt: next(answers),
        print_fn=output.append,
        checker=lambda _host, _port: True,
    )
    assert result == 9090
    assert INVALID_PORT_MESSAGE in output


def test_uppercase_menu_input_is_accepted():
    answers = iter(["Y"])
    assert select_port_interactive(
        "0.0.0.0",
        input_fn=lambda _prompt: next(answers),
        print_fn=lambda _line: None,
        checker=lambda _host, port: port == 8080,
    ) == 8080
