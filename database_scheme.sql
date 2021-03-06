/*
 "Crying operator" is an operator, who is currently using bot as a client
 */

CREATE TABLE users
(
    tg_id       integer NOT NULL PRIMARY KEY,
    local_id    serial UNIQUE,
    is_operator BOOLEAN NOT NULL DEFAULT FALSE,
    is_admin    BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE conversations
(
    client_id   integer NOT NULL UNIQUE REFERENCES users,
    operator_id integer NOT NULL UNIQUE REFERENCES users
);

CREATE TABLE reflected_messages
(
    sender_chat_id      integer NOT NULL REFERENCES users (tg_id),
    sender_message_id   integer NOT NULL,
    receiver_chat_id    integer NOT NULL REFERENCES users (tg_id),
    receiver_message_id integer NOT NULL
);


CREATE FUNCTION user_is_operator(integer) RETURNS boolean
AS
'SELECT is_operator
 FROM users WHERE tg_id=$1' LANGUAGE SQL VOLATILE;

CREATE FUNCTION operator_is_operating(integer) RETURNS boolean
AS
'SELECT exists(SELECT 1
               FROM conversations
               WHERE operator_id = $1)' LANGUAGE SQL VOLATILE;

CREATE FUNCTION operator_is_crying(integer) RETURNS boolean
AS
'SELECT exists(SELECT 1
               FROM conversations
               WHERE client_id = $1)'
    LANGUAGE SQL VOLATILE;


ALTER TABLE conversations
    ADD CONSTRAINT client_is_not_operating CHECK ( NOT user_is_operator(client_id) OR
                                                   NOT operator_is_operating(client_id) );

ALTER TABLE conversations
    ADD CONSTRAINT operator_is_operator_and_is_not_crying CHECK ( user_is_operator(operator_id) AND
                                                                  NOT operator_is_crying(operator_id) );

ALTER TABLE conversations
    ADD CONSTRAINT client_and_operator_are_different CHECK ( client_id <> operator_id );
